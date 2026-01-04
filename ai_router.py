import json
import re
from typing import Any, Dict, Optional, Tuple
from openai import OpenAI
from anthropic import Anthropic



DEFAULT_AGENT_JSON_SCHEMA: Dict[str, Any] = {
    "name": "agent_output",
    "schema": {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "action": {"type": "string", "enum": ["respond", "tool_call"]},
            "message": {"type": "string"},
            "tool": {"type": "string"},
            "args": {"type": "object"},
        },
        "required": ["action", "message"],
    },
}

class AIRouter:
    def __init__(self, key_openai: str = None, key_anthropic: str = None):
        if key_openai is None:
            self.openai = None
        else:
            self.openai = OpenAI(api_key=key_openai)
        
        if key_anthropic is None:
            self.anthropic = None
        else:
            self.anthropic = Anthropic(api_key=key_anthropic)

    def ask(self, system_prompt, message_list, model="gemma3:4b", max_tokens=64000):
        """
        model:
          - auto: 用途に応じて自動
          - gpt: GPT固定
          - claude: Claude固定
        """
        try:
            if model[0:3] == "gpt":
                message_list.append({'role': 'system', 'content': system_prompt})
                return self._ask_gpt(model, message_list, max_tokens)
            elif model[0:6] == "claude":
                return self._ask_claude(model, system_prompt, message_list, max_tokens)
            elif model[0:6] == "ollama" or model[0:5] == "gemma" or model[0:4] == "qwen":
                return self._ask_ollama(model, system_prompt, message_list, max_tokens)
        except Exception as e:
            print(f"Error with {model}, fallback to the other model:", e)

    def _ask_gpt(self, model, message_list, max_tokens):
        if self.openai is None:
            print('no valid key for OpenAI')
            return None, None, 0, 0
        if 'gpt-4' in model or 'gpt-3' in model or 'o3' in model or 'o4' in model:
            res = self.openai.chat.completions.create(
                model=model, 
                messages=message_list
            )
            return res.choices[0].message.content, res.model, res.usage.prompt_tokens, res.usage.total_tokens
        else:
            res = self.openai.responses.create(
                model=model,
                input=message_list,
                max_output_tokens=max_tokens
            )
            messages = [d for d in res.output if d.type == 'message']
            return messages[0].content[0].text, res.model, res.usage.input_tokens, res.usage.output_tokens

    def _ask_claude(self, model, system_prompt, message_list, max_tokens):
        # ストリーミング開始
        if self.anthropic is None:
            print('no valid key for Claude')
            return None, None, 0, 0

        with self.anthropic.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt, 
            messages=message_list
        ) as stream:

            text = ""
            model_name = None
            input_tokens = 0
            output_tokens = 0

            for event in stream:
                # テキスト部分の逐次取得
                if event.type == "content_block_delta":
                    text += event.delta.text

                # 開始時に model 名が送られる
                elif event.type == "message_start":
                    model_name = event.message.model

                # 終了時に usage が入る
                elif event.type == "message_stop":
                    usage = event.message.usage
                    input_tokens = usage.input_tokens
                    output_tokens = usage.output_tokens

            return text, model_name, input_tokens, output_tokens
    
    def _ask_ollama(self, model, system_prompt, message_list, max_tokens):
        # OLLAMA API 呼び出し
        import requests

        url = f"http://localhost:11434/v1/chat/completions"
        headers = {
            "Content-Type": "application/json"
        }

        messages = [{'role': 'system', 'content': system_prompt}] + message_list

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False
        }

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        res_json = response.json()

        text = res_json['choices'][0]['message']['content']
        model_name = res_json['model']
        return text, model_name, res_json['usage']['prompt_tokens'], res_json['usage']['completion_tokens']



    def ask_json(
        self,
        system_prompt: str,
        user_text: str,
        schema: Dict[str, Any] = DEFAULT_AGENT_JSON_SCHEMA,
        model: str = "gemma3:4b",
        max_tokens: int = 32000,
    ) -> Dict[str, Any]:
        """
        入力は user_text(str) のみ。
        出力は常に dict（AgentResponse）。
        """
        try:
            if model.startswith(("gpt", "o3", "o4")):
                return self._ask_gpt_json(model, system_prompt, user_text, max_tokens, schema)
            elif model.startswith("claude"):
                return self._ask_claude_json(model, system_prompt, user_text, max_tokens, schema)
            elif model.startswith(("ollama", "gemma", "qwen")):
                return self._ask_ollama_json(model, system_prompt, user_text, max_tokens, schema)
            else:
                return {
                    "ok": False,
                    "provider": "unknown",
                    "model": model,
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "raw_text": "",
                    "json": None,
                    "error": f"Unknown model prefix: {model}",
                }
        except Exception as e:
            return {
                "ok": False,
                "provider": "unknown",
                "model": model,
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "raw_text": "",
                "json": None,
                "error": str(e),
            }

    # -----------------------------
    # OpenAI
    # -----------------------------
    def _ask_gpt_json(
        self,
        model: str,
        system_prompt: str,
        user_text: str,
        max_tokens: int,
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        まず responses + json_schema で JSON強制（推奨）
        失敗したら chat.completions + json_object を試す（モデルが対応していれば）
        それもダメならプロンプト強制 + 抽出/パース
        """
        if self.openai is None:
            print('no valid key for OpenAI')
            return None, None, 0, 0

        input_messages = []
        if system_prompt:
            input_messages.append({"role": "system", "content": system_prompt})
        input_messages.append({"role": "user", "content": user_text})

        try:
            res = self.openai.chat.completions.create(
                model=model,
                response_format={"type": "json_schema", "json_schema": schema},
                max_completion_tokens=max_tokens,
                messages=input_messages
            )
#            outputs = [d for d in res.output if getattr(d, "type", None) == "message"]
            raw_text = res.choices[0].message.content if res else ""
            usage = res.usage
            input_tokens  = usage.prompt_tokens
            output_tokens = usage.completion_tokens
            total_tokens  = usage.total_tokens
            js, err = self._parse_json_safely(raw_text)
            return {
                "ok": err is None and js is not None,
                "provider": "openai",
                "model": res.model,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                },
                "raw_text": raw_text,
                "json": js,
                "error": err,
            }
        except Exception as e:
            return {
                "ok": False,
                "provider": "openai",
                "model": model,
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "raw_text": "",
                "json": None,
                "error": f"OpenAI failed: {str(e)}",
            }

    # -----------------------------
    # Claude
    # -----------------------------
    def _ask_claude_json(
        self,
        model: str,
        system_prompt: str,
        user_text: str,
        max_tokens: int,
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        if self.anthropic is None:
            print('no valid key for Claude')
            return None, None, 0, 0

        json_only_system = (
            (system_prompt or "")
            + "\n\nYou MUST output ONLY valid JSON (no prose, no markdown). "
            + "Follow this JSON shape:\n"
            + json.dumps(schema["schema"], ensure_ascii=False)
        )

        messages = [{"role": "user", "content": user_text}]

        with self.anthropic.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=json_only_system,
            messages=messages,
        ) as stream:
            text = ""
            model_name = None
            input_tokens = 0
            output_tokens = 0

            for event in stream:
                if event.type == "content_block_delta":
                    text += event.delta.text
                elif event.type == "message_start":
                    model_name = event.message.model
                elif event.type == "message_stop":
                    usage = event.message.usage
                    input_tokens = usage.input_tokens
                    output_tokens = usage.output_tokens

        cand = self._extract_json(text) or text
        js, err = self._parse_json_safely(cand)

        return {
            "ok": err is None and js is not None,
            "provider": "anthropic",
            "model": model_name or model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": (input_tokens + output_tokens),
            },
            "raw_text": cand,
            "json": js,
            "error": err,
        }

    # -----------------------------
    # Ollama (OpenAI互換API)
    # -----------------------------
    def _ask_ollama_json(
        self,
        model: str,
        system_prompt: str,
        user_text: str,
        max_tokens: int,
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        import requests

        url = "http://localhost:11434/v1/chat/completions"
        headers = {"Content-Type": "application/json"}

        json_only_system = (
            (system_prompt or "")
            + "\n\nYou MUST output ONLY valid JSON (no prose, no markdown). "
            + "Follow this JSON shape:\n"
            + json.dumps(schema["schema"], ensure_ascii=False)
        )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": json_only_system},
                {"role": "user", "content": user_text},
            ],
            "max_tokens": max_tokens,
            "stream": False,
        }

        r = requests.post(url, headers=headers, json=payload, timeout=300)
        r.raise_for_status()
        res_json = r.json()

        raw_text = res_json["choices"][0]["message"]["content"] or ""
        cand = self._extract_json(raw_text) or raw_text
        js, err = self._parse_json_safely(cand)

        usage = res_json.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))

        return {
            "ok": err is None and js is not None,
            "provider": "ollama",
            "model": res_json.get("model", model),
            "usage": {
                "input_tokens": prompt_tokens,
                "output_tokens": completion_tokens,
                "total_tokens": (prompt_tokens + completion_tokens),
            },
            "raw_text": cand,
            "json": js,
            "error": err,
        }




    def _extract_json(self, text: str) -> Optional[str]:
        """Claude/Ollamaなどが余計な文を混ぜた時に、JSON部分だけ抜く簡易抽出。"""
        if not text:
            return None

        t = text.strip()
        if t.startswith("{") and t.endswith("}"):
            return t

        # ```json ... ```
        m = re.search(r"```json\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if m:
            cand = m.group(1).strip()
            if cand.startswith("{") and cand.endswith("}"):
                return cand

        # 最初の { から最後の } まで
        i = text.find("{")
        j = text.rfind("}")
        if i != -1 and j != -1 and i < j:
            return text[i : j + 1].strip()

        return None


    def _parse_json_safely(self, text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            js = json.loads(text)
            if isinstance(js, Dict):
                return js, None
            return None, "JSON is not an object"
        except Exception as e:
            return None, str(e)
