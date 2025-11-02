"""
EPFL Assessment Agent

Final holistic assessment of EPFL relationship that runs after all enrichments complete.
"""

import logging
from typing import Any, Dict

from ..data_models import EPFLAssessmentResult
from ..llm.model_config import (
    load_model_config,
    validate_config,
)
from .agents_management import run_agent_with_fallback
from .epfl_assessment_prompts import (
    epfl_assessment_system_prompt,
    get_user_epfl_assessment_prompt,
)

# Setup logger
logger = logging.getLogger(__name__)

# Load model configuration
epfl_assessment_configs = load_model_config("run_llm_analysis")

# Validate configurations
for config in epfl_assessment_configs:
    if not validate_config(config):
        logger.error(f"Invalid configuration for EPFL assessment: {config}")
        raise ValueError("Invalid model configuration")


async def assess_epfl_relationship(
    data: Dict[str, Any],
    item_type: str,
) -> Dict[str, Any]:
    """
    Perform final holistic EPFL relationship assessment.
    
    This function runs AFTER all enrichments complete and reviews ALL collected
    data to make a final determination about EPFL relationship with proper
    confidence scoring and comprehensive justification.
    
    Args:
        data: Complete data object (dict) containing all metadata
        item_type: Type of item ("user", "organization", or "repository")
        
    Returns:
        Dictionary with:
        - data: EPFLAssessmentResult with final assessment
        - usage: Token usage statistics
    """
    logger.info(f"Starting final EPFL assessment for {item_type}")
    
    # Create context for the agent
    agent_context = {
        "item_type": item_type,
        "data": data,
    }
    
    # Prepare the prompt
    prompt = get_user_epfl_assessment_prompt(item_type, data)
    
    try:
        # No tools needed for this assessment - it's analyzing existing data
        tools = []
        
        # Run agent with fallback across multiple models
        result = await run_agent_with_fallback(
            epfl_assessment_configs,
            prompt,
            agent_context,
            EPFLAssessmentResult,  # Output type
            epfl_assessment_system_prompt,
            tools,
        )
        
        # Extract the output from PydanticAI result
        if hasattr(result, "output"):
            assessment_data = result.output
        else:
            assessment_data = result
        
        # Ensure it's properly typed
        if isinstance(assessment_data, dict):
            assessment_data = EPFLAssessmentResult(**assessment_data)
        elif hasattr(assessment_data, "model_dump"):
            # Already an EPFLAssessmentResult
            pass
        
        logger.info(f"EPFL assessment completed for {item_type}: "
                   f"relatedToEPFL={assessment_data.relatedToEPFL}, "
                   f"confidence={assessment_data.relatedToEPFLConfidence:.2f}")
        
        # Return with usage statistics
        return {
            "data": assessment_data,
            "usage": {
                "input_tokens": getattr(result, "input_tokens", 0),
                "output_tokens": getattr(result, "output_tokens", 0),
                "estimated_input_tokens": 0,
                "estimated_output_tokens": 0,
            }
        }
        
    except Exception as e:
        logger.error(f"Error in EPFL assessment for {item_type}: {e}", exc_info=True)
        # Return a fallback assessment with low confidence
        fallback_assessment = EPFLAssessmentResult(
            relatedToEPFL=False,
            relatedToEPFLConfidence=0.0,
            relatedToEPFLJustification=f"EPFL assessment failed due to error: {str(e)}",
            evidenceItems=[],
        )
        return {
            "data": fallback_assessment,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_input_tokens": 0,
                "estimated_output_tokens": 0,
            }
        }

