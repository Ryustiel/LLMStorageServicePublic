"""
Document processing code.
"""

from email.mime import text
import os, asyncio, pydantic, base64, mistralai, langchain_openai

from typing import Dict, List, Literal, Optional
from langchain_core.messages import HumanMessage, AIMessage


if not "MISTRAL_API_KEY" in os.environ or not "OPENAI_API_KEY" in os.environ:
    raise ValueError("MISTRAL_API_KEY or OPENAI_API_KEY environment variable not set.")

mistral_api_key = os.environ["MISTRAL_API_KEY"]
openai_api_key = pydantic.SecretStr(os.environ["OPENAI_API_KEY"])

MISTRAL_CLIENT = mistralai.Mistral(api_key=mistral_api_key)
LANGCHAIN_OPENAI_RUNNABLE = langchain_openai.ChatOpenAI(api_key=openai_api_key, model="gpt-5-mini", reasoning_effort="minimal")


async def summarize_document(raw_ocr: str) -> str:
    if len(raw_ocr) < 300:
        return raw_ocr  # No need to summarize short texts

    response = await LANGCHAIN_OPENAI_RUNNABLE.ainvoke(
        [
            HumanMessage(content=f"Summarize the following document in a concise manner, focusing on key points and important details:\n\n{raw_ocr}")
        ]
    )
    if not isinstance(response.content, str):
        raise ValueError(f"Unexpected response type from OpenAI model: {type(response.content)}")
    return response.content


async def process_b64_image(url: str, is_raw_base64: bool = False) -> str:
    """Process a base64-encoded image (to utf-8 string) using OpenAI's vision model."""

    if is_raw_base64:
        url = f"data:image/png;base64,{url}"
    
    response = await LANGCHAIN_OPENAI_RUNNABLE.ainvoke(
        [
            HumanMessage(
                content=[
                    {"type": "text", "text": "Describe this image. Complex, textual or schematic images should have more details to reflect their full content. Do not output anything other than description."}, 
                    {"type": "image_url", "image_url": {"url": url}}
                ]
            )
        ]   
    )
    if not isinstance(response.content, str):
        raise ValueError(f"Unexpected response type from OpenAI vision model: {type(response.content)}")
    
    return response.content


async def process_document(
    file_data: bytes, 
    mime_type: str, 
    summarize_images: bool = True
) -> str:
    """
    Process a document using Mistral OCR API.

    Parameters:
        path (str): Path or URL to the document.

    Returns:
        str: Compiled markdown representation of the document and its images.
    """

    encoded_file = base64.b64encode(file_data).decode('utf-8')
    
    if mime_type.startswith("image/"):
        
        image_summary = await process_b64_image(f"data:{mime_type};base64,{encoded_file}")
        return image_summary
    
        # TODO : Raise a special flag if the image is a complex document (pdf screenshot style)
        # So that it can be passed to the OCR instead.

    elif mime_type == "application/pdf":
        
        document: mistralai.DocumentTypedDict = {
            "type": "document_url",
            "document_url": f"data:application/pdf;base64,{encoded_file}"
        }

        response = await MISTRAL_CLIENT.ocr.process_async(
            model="mistral-ocr-latest",
            document=document,
            include_image_base64=True
        )
        
        # 4. If image boxes were found, process each image box independently
        
        image_id = lambda page_index, image_index: f"page_{page_index}_image_{image_index}"
        summary_tasks: Dict[str, asyncio.Task[str]] = {}
        
        if summarize_images:

            if response:
                for page in response.pages:
                    for image in page.images:
                        if image.image_base64:
                            task = process_b64_image(image.image_base64)
                            summary_tasks[image_id(page.index, image.id)] = asyncio.create_task(task)

            print(f"Found {len(summary_tasks)} valid image boxes in the document.")

            await asyncio.gather(*summary_tasks.values())

        # 5. Integrate the processed image boxes into the main markdown representation

        # Iterate over the pages and images, replace the image boxes in the markdown with the processed summaries.

        compiled_markdown = ""
        for page in response.pages:
            compiled_markdown += f"\n\n(Page {page.index + 1})\n\n"
            
            # Replace image boxes with processed summaries
            for image in page.images:
                if image.image_base64:
                    img_id = image_id(page.index, image.id)
                    if img_id in summary_tasks:
                        summary = summary_tasks[img_id].result()
                    else:
                        summary = "(No summary available)"
                    page.markdown = page.markdown.replace(f"![{image.id}]", f"![{summary}]")
            
            compiled_markdown += page.markdown + "\n"
        
        return compiled_markdown
        
    else:
        raise ValueError(f"Unsupported MIME type for OCR processing: {mime_type}")


if __name__ == "__main__":
    
    with open("test_attachments/2074_medes2016publie.pdf", "rb") as f:
        file_bytes = f.read()
    task = process_document(file_bytes, mime_type="application/pdf")
    response = asyncio.run(task)
    print(response)
