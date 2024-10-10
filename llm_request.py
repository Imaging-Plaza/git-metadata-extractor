from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate
from github_request import GithubRequest
from pydantic import BaseModel, Field

from utils import to_bool

BASE_URL = "http://ma8a15954cdb8.dyn.epfl.ch:11434"
MODEL_NAME = "llama3.2"
TOKEN = "token"
ORGANISATION = "paulscherrerinstitute"

class Output(BaseModel):
    repo: str = Field(description="The repo of the project")
    reply: bool = Field(description="If it is an imaging software or not")

class Model:
    def __init__(self, base_url, model_name):
        self.model = OllamaLLM(base_url=base_url, model=model_name)#.with_structured_output(Output)

        self.template = """Question: {question}

                        Answer: Let's think step by step."""
                        
        self.prompt = ChatPromptTemplate.from_template(self.template)
        self.chain = self.prompt | self.model

    def request_question(self, question):
        return self.chain.invoke({"question": question})
    
repos = GithubRequest(token=TOKEN).request_repos(ORGANISATION)

model = Model(base_url=BASE_URL, model_name=MODEL_NAME)

question = """I need you to analyse this GitHub repository: {}? Does this repository count as an imaging software? Here is what “counts” as an imaging software?
            1. A software PRIMARLY used to analyse images or that has to do with image data (it is its main function)
            2. It should be a high-level software (we are not interested in instruments and optics software)
            3. It should be a SOFTWARE that can be used, not just something related to images
            First analyse the README and if you are not sure look at the code.
            You should only reply True if we count it as an imaging software or False if not. No more text.
            """

imaging_software = {}
for repo in repos:
    reply = model.request_question(question.format(repo))
    boolean_reply = to_bool(reply)
    imaging_software[repo] = boolean_reply
    print(f"Repo url : {repo} | Imaging software : {reply}")

print(f"Number of imaging softwares: {sum(imaging_software.values())}.\nRatio: {sum(imaging_software.values())/len(imaging_software.values())*100}%")