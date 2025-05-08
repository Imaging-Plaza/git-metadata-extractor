from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
from .core.gimie_methods import extract_gimie
from .core.genai_model import llm_request_repo_infos
from .utils.utils import merge_jsonld



app = FastAPI()

@app.get("/")
def index():
    return {"title": "Hello, welcome to the Git Metadata Extractor v0.0.1. Gimie Version 0.7.2. "}

@app.get("/v1/extract/{full_path:path}")
async def extract(full_path:str):
    # First Gimie
    jsonld_gimie_data = extract_gimie(full_path)

    llm_result = llm_request_repo_infos(full_path)

    merged_results = merge_jsonld(jsonld_gimie_data, llm_result)

    return {"link": full_path, "output": merged_results}
    
@app.get("/v1/gimie/{full_path:path}")
async def gimie(full_path:str, 
                format:str = "jsonld", 
                serialize:bool = True):
    try:
        result = extract_gimie(full_path, format=format, serialize=serialize)
        return result
    except Exception as e:
        return {"link": full_path, "output": str(e)}
    

@app.get("/v1/llm/{full_path:path}")
async def llm(full_path:str):
    # Only LLM
    pass

@app.exception_handler(ValueError)
async def value_error_exception_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"message": str(exc)},
    )