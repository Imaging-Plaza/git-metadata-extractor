from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os
from .core.gimie_methods import extract_gimie
from .core.models import convert_jsonld_to_pydantic, convert_pydantic_to_zod_form_dict
from .core.genai_model import llm_request_repo_infos
from .utils.utils import merge_jsonld



app = FastAPI()

@app.get("/")
def index():
    return {"title": "Hello, welcome to the Git Metadata Extractor v0.1.0. Gimie Version 0.7.2. "}

@app.get("/v1/extract/json/{full_path:path}")
async def extract(full_path:str):

    jsonld_gimie_data = extract_gimie(full_path, format="json-ld")

    try:
        llm_result = llm_request_repo_infos(str(full_path))
    except Exception as e:
        raise HTTPException(
            status_code=424, 
            detail=f"Error from LLM service: {e}"
        )

    merged_results = merge_jsonld(jsonld_gimie_data, llm_result)

    pydantic_data = convert_jsonld_to_pydantic(merged_results["@graph"])

    zod_data = convert_pydantic_to_zod_form_dict(pydantic_data)

    return {"link": full_path, 
            "output": zod_data}

@app.get("/v1/extract/json-ld/{full_path:path}")
async def extract(full_path:str):

    jsonld_gimie_data = extract_gimie(full_path, format="json-ld")

    try:
        llm_result = llm_request_repo_infos(str(full_path))
    except Exception as e:
        raise HTTPException(
            status_code=424, 
            detail=f"Error from LLM service: {e}"
        )

    merged_results = merge_jsonld(jsonld_gimie_data, llm_result)

    return {"link": full_path, 
            "output": merged_results}
    
@app.get("/v1/gimie/{full_path:path}")
async def gimie(full_path:str, 
                format:str = "json-ld"):
    try:
        gimie_output = extract_gimie(full_path, format=format)
    except Exception as e:
        raise HTTPException(
            status_code=424, #?
            detail=f"Error from LLM service: {e}"
        )
    
    return {"link": full_path, 
            "output": gimie_output}

@app.get("/v1/llm/json-ld/{full_path:path}")
async def llm(full_path:str):

    try:
        llm_result = llm_request_repo_infos(str(full_path))
    except Exception as e:
        raise HTTPException(
            status_code=424, 
            detail=f"Error from LLM service: {e}"
        )
    
    return {"link": full_path, 
            "output": llm_result}

@app.get("/v1/llm/json/{full_path:path}")
async def llm(full_path:str):

    try:
        llm_result = llm_request_repo_infos(str(full_path), output_format="json")
    except Exception as e:
        raise HTTPException(
            status_code=424, 
            detail=f"Error from LLM service: {e}"
        )
    
    return {"link": full_path, 
            "output": llm_result}

@app.exception_handler(ValueError)
async def value_error_exception_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"message": str(exc)},
    )