#!/usr/bin/env python
# coding=utf-8

import json

import requests


def chat(prompt, endpoint_url, model_id, auth_key, timeout=120):
    """Call an OpenAI-compatible chat completion endpoint.

    The endpoint configuration is intentionally kept outside this module, in
    the external YAML file, so users only need to provide:
      - endpoint_url
      - model_id
      - auth_key
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Authorization": f"Bearer {auth_key}",
    }
    payload = {
        "model": model_id,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "enable_thinking": False,
    }

    response = requests.post(
        endpoint_url,
        headers=headers,
        data=json.dumps(payload),
        timeout=timeout,
    )
    response.raise_for_status()
    response_data = response.json()

    try:
        content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected LLM response: {response_data!r}") from exc

    if isinstance(content, list):
        content = "".join(
            piece.get("text", "") if isinstance(piece, dict) else str(piece)
            for piece in content
        )
    return str(content).strip()
