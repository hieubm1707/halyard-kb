"""Ask the assistant a question: system prompt + File Search over the synced store.

Usage: python ask.py "How do I add a YouTube video?"
"""

import os
from pathlib import Path
import sys

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-3.6-flash"
SYSTEM_PROMPT = (Path(__file__).parent / "docs" / "system-prompt.txt").read_text()


def main() -> int:
    question = " ".join(sys.argv[1:]) or "How do I add a YouTube video?"
    api_key = os.getenv("API_KEY") or os.getenv("GEMINI_API_KEY")
    store_name = os.getenv("FILE_SEARCH_STORE")
    if not api_key or not store_name:
        print("Set API_KEY and FILE_SEARCH_STORE (printed in the sync log).", file=sys.stderr)
        return 2

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[
                types.Tool(
                    file_search=types.FileSearch(file_search_store_names=[store_name])
                )
            ],
        ),
    )

    print(f"Q: {question}\n\n{response.text}")
    grounding = response.candidates[0].grounding_metadata if response.candidates else None
    titles = {
        chunk.retrieved_context.title
        for chunk in (grounding.grounding_chunks or [] if grounding else [])
        if chunk.retrieved_context
    }
    print("\nRetrieved from:", ", ".join(sorted(t for t in titles if t)) or "nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
