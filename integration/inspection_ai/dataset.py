from typing import Any
from pathlib import Path
from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset, Sample
from inspect_ai.solver import solver, TaskState, Generate
from inspect_ai.scorer import CORRECT, Score, Target, accuracy, stderr, scorer
from scicode.parse.parse import extract_function_name, get_function_from_code
from scicode.gen.models import generate_dummy_response, extract_python_script

SAVE = True
TEMP_DIR = "./tmp"
MODEL_NAME = "gpt-4o"
WITH_BACKGROUND = False
DEFAULT_PROMPT_TEMPLATE = Path("data", "background_comment_template.txt").read_text()
BACKGOUND_PROMPT_TEMPLATE = Path("data", "multistep_template.txt").read_text()

SCICODE_DATA_JSON_PATH = "/eagle/tpc/zilinghan/SciCode/integration/inspection_ai/data/problems_all_new.json"
# SCICODE_DATA_JSON_PATH = "/eagle/tpc/zilinghan/SciCode/integration/inspection_ai/data/problems_dev.json"
# SCICODE_DATA_JSON_PATH = "/eagle/tpc/zilinghan/SciCode/integration/inspection_ai/data/problems_all.json

class PromptingAssistant:
    def __init__(
        self,
        output_dir: Path,
        prompt_dir: Path,
        with_background: bool,
    ):
        self.output_dir = output_dir
        self.prompt_dir = prompt_dir
        self.with_background = with_background
        self.previous_llm_code = []
        
    def _get_background_dir(self):
        return "with_background" if self.with_background else "without_background"
    
    def register_previous_response(
        self,
        prob_data: dict,
        response: str,
        previous_code: str,
        num_steps: int,
    ):
        self.previous_llm_code[num_steps - 1] = extract_python_script(response)
        self.save_response_with_steps(
            prob_data,
            response,
            previous_code,
            num_steps,
        )
    
    def save_response_with_steps(
        self, 
        prob_data: dict, 
        response: str,
        previous_code: str, 
        num_steps: int
    ) -> None:
        output_dir = Path(
            self.output_dir,
            self._get_background_dir()
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        prob_id = prob_data["problem_id"]
        output_file_path = output_dir / f"{prob_id}.{num_steps}.py"
        python_code = extract_python_script(response)
        output_file_path.write_text(f'{previous_code}\n{python_code}', encoding="utf-8")    
    
    @staticmethod
    def process_problem_code(
        prob_data: dict, 
        num_steps: int
    ) -> str:
        header_docstring = prob_data['sub_steps'][num_steps - 1]['function_header']
        return_str = prob_data['sub_steps'][num_steps - 1]['return_line']
        string = f"{header_docstring}\n\n{return_str}"
        return string
    
    def process_problem_steps(
        self, 
        problem_data: dict, 
        num_steps: int
    ):
        """Process problem data and return previous steps and next steps"""
        output_lines = []
        next_step = []
        previous_code = []
        for i in range(num_steps - 1):
            output_lines.append(problem_data["sub_steps"][i]["step_description_prompt"] + '\n' +
                                problem_data["sub_steps"][i]["step_background"] if self.with_background
                                else problem_data["sub_steps"][i]["step_description_prompt"])
            output_lines.append(self.previous_llm_code[i])
            previous_code.append(self.previous_llm_code[i])
            output_lines.append("------")

        next_step.append(problem_data["sub_steps"][num_steps - 1]["step_description_prompt"] + '\n' +
                         problem_data["sub_steps"][num_steps - 1]["step_background"] if self.with_background
                         else problem_data["sub_steps"][num_steps - 1]["step_description_prompt"])
        next_step.append(self.process_problem_code(problem_data, num_steps))
        output_str = "\n\n".join(output_lines[:-1])  # Remove the last "------"
        next_step_str = "\n\n".join(next_step)
        previous_code_str = "\n".join(previous_code)
        return output_str, next_step_str, previous_code_str
    
    def generate_prompt_with_steps(
        self,
        prob_data: dict,
        num_steps: int,
        prompt_template=DEFAULT_PROMPT_TEMPLATE,
    ):
        # parse the input file and extract the content
        problem_steps_str, next_step_str, previous_code_str = self.process_problem_steps(prob_data, num_steps)
        dependencies = prob_data["required_dependencies"]
        assert next_step_str
        return prompt_template.format(
            problem_steps_str=problem_steps_str,
            next_step_str=next_step_str,
            dependencies=dependencies,
        ), f'{dependencies}\n{previous_code_str}\n'
    
    def save_prompt_with_steps(
            self, 
            prob_data: dict, 
            prompt: str, 
            num_steps: int
        ) -> None:
        output_dir = Path(self.prompt_dir, self._get_background_dir())
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file_path = output_dir / f"{prob_data['problem_id']}.{num_steps}.txt"
        output_file_path.write_text(prompt, encoding="utf-8")

    def prepare_final_prompt_with_steps(
        self,
        prob_data: dict,
        num_steps: int,
        tot_steps: int,
        prompt_template=DEFAULT_PROMPT_TEMPLATE,
        *,
        save: bool = True
    ):
        prob_id = prob_data["problem_id"]
        output_file_path = Path(
            self.output_dir, 
            self._get_background_dir(),
            f"{prob_id}.{num_steps}.py"
        )
        if num_steps == 1:
            self.previous_llm_code = [None] * tot_steps
        else:
            if len(self.previous_llm_code) != tot_steps:
                self.previous_llm_code = [None] * tot_steps
            for prev_step in range(num_steps - 1):
                if self.previous_llm_code[prev_step] is None:
                    if (
                        (prob_id == "13" and prev_step == 5) or 
                        (prob_id == "62" and prev_step == 0) or 
                        (prob_id == "76" and prev_step == 2)
                    ):
                        prev_file_path = Path(
                            "data",
                            f"{prob_id}.{prev_step+1}.txt"
                        )
                    else:
                        prev_file_path = Path(
                            self.output_dir,
                            self._get_background_dir(),
                            f"{prob_id}.{prev_step + 1}.py"
                        )
                    if prev_file_path.is_file():
                        prev_file_content = prev_file_path.read_text(encoding='utf-8')
                        func_name = extract_function_name(
                            prob_data["sub_steps"][prev_step]["function_header"]
                        )
                        function_code = get_function_from_code(
                            prev_file_content, func_name
                        )
                        self.previous_llm_code[prev_step] = function_code
                        print(f'Loaded previous code for problem {prob_id} step {prev_step + 1}')
                    else:
                        raise Exception(f'Generating problem {prob_id} step {num_steps} ahead of step {prev_step + 1}.')
                
        prompt, previous_code = self.generate_prompt_with_steps(
            prob_data,
            num_steps,
            prompt_template,
        )
        if save:
            self.save_prompt_with_steps(
                prob_data,
                prompt,
                num_steps,
            )
        return prompt, previous_code
            

def record_to_sample(record):
    return Sample(
        input="problem_id",
        target=record["problem_id"],
        id=record["problem_id"],
        metadata={
            k: v for k, v in record.items()
        }
    )

dataset = json_dataset(
    SCICODE_DATA_JSON_PATH, 
    record_to_sample
)
    

@solver
def dummy_solver(**params: dict[str, Any]):
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        prompt_assistant = PromptingAssistant(
            output_dir=Path(TEMP_DIR, "generated_code"),
            prompt_dir=Path(TEMP_DIR, "prompt"),
            with_background=WITH_BACKGROUND,
        )
        prompt_template = BACKGOUND_PROMPT_TEMPLATE if WITH_BACKGROUND else DEFAULT_PROMPT_TEMPLATE
        print('===============================')
        print(f'Processing problem {state.sample_id}')
        sub_steps = state.metadata["sub_steps"]
        for idx in range(len(sub_steps)):
            prob_id = state.metadata["problem_id"]
            if (
                (prob_id == "13" and idx == 5) or
                (prob_id == "62" and idx == 0) or
                (prob_id == "76" and idx == 2)
            ):
                continue
            prompt, previous_code = prompt_assistant.prepare_final_prompt_with_steps(
                prob_data=state.metadata,
                num_steps=idx+1,
                tot_steps=len(sub_steps),
                prompt_template=prompt_template,
            )
            response_from_llm = generate_dummy_response(prompt)
            prompt_assistant.register_previous_response(
                prob_data=state.metadata,
                response=response_from_llm,
                previous_code=previous_code,
                num_steps=idx+1,
            )
        print('===============================')
        return state

    return solve


@scorer(metrics=[accuracy(), stderr()])
def dummy_scorer():
    async def score(state: TaskState, target: Target):
        return Score(
            value=CORRECT
        )

    return score

@task
def dummy_task():
    return Task(
        dataset=dataset,
        solver=dummy_solver(),
        scorer=dummy_scorer(),
    )
