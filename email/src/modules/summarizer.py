"""
Orchestrate calls to the LLM to process emails documents and generate summaries.
Also handle email interpretation context.
"""

import os, langchain_openai

from typing import List
import modules.types as types

summary_model = langchain_openai.ChatOpenAI(model="gpt-5-mini", api_key=os.environ["OPENAI_API_KEY"])

async def summarize(raw_input: str) -> str:
    """
    Summarize the email content using an LLM.
    """
    response = await summary_model.ainvoke((
        "Summarize the following emails as a daily digest. "
        + "Write in the main language of the emails:\n\n"
        + raw_input
    ))
    if not isinstance(response.content, str):
        raise RuntimeError("Unexpected response from langchain. Response.content = " + str(response.content))
    return response.content
