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
    return {"title": "Hello, welcome to the Git Metadata Extractor v0.0.1. Gimie Version 0.7.2. "}

@app.get("/v1/extract/{full_path:path}")
async def extract(full_path:str):
    # TODO: Add query parameter for json-ld output

    jsonld_gimie_data = extract_gimie(full_path, format="json-ld")

    try:
        llm_result = llm_request_repo_infos(str(full_path))
    except Exception as e:
        # Handle failures from the LLM service, like the 403 key limit error.
        raise HTTPException(
            status_code=424, # Failed Dependency
            detail=f"Error from LLM service: {e}"
        )

    merged_results = merge_jsonld(jsonld_gimie_data, llm_result)

    pydantic_data = convert_jsonld_to_pydantic(merged_results["@graph"])

    zod_data = convert_pydantic_to_zod_form_dict(pydantic_data)


    return {"link": full_path, 
            "output": zod_data}

    # return {"link": full_path, 
    #         "output": zod_data,
    #         "gimie_jsonld": jsonld_gimie_data,
    #         "output_jsonld": llm_result}
    
@app.get("/v1/gimie/{full_path:path}")
async def gimie(full_path:str, 
                format:str = "json-ld"):
    try:
        result = extract_gimie(full_path, format=format)
        return result
    except Exception as e:
        return {"link": full_path, "output": e }
    

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