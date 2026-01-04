from dataclasses import dataclass
from ai_router import AIRouter


# -------------------------
# Data models
# -------------------------
@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __str__(self) -> str:
        return (
            f"\n"
            f"  input_tokens: {self.input_tokens}\n"
            f"  output_tokens: {self.output_tokens}\n"
            f"  total_tokens: {self.total_tokens}\n"
        )


@dataclass
class MinuteReportItem:
    topic: str
    topic_relevance_score: int
    topic_related_summary: str

    def __str__(self) -> str:
        return (
            f"\n"
            f"  トピック: {self.topic}\n"
            f"    関連度合い: {self.topic_relevance_score}\n"
            f"    議事要約: {self.topic_related_summary}\n"
        )


@dataclass
class MinuteReport:
    provider: str
    model: str
    usage: Usage
    raw_text: str
    items: list[MinuteReportItem]

    def __str__(self) -> str:
        items_text = "\n".join(map(str, self.items))
        return (
            f"\n"
            f"AIプロバイダ: {self.provider}\n"
            f"AIモデル: {self.model}\n"
            f"使用量: {self.usage}"
            f"関連議事録: {items_text}\n"
        )


# -------------------------
# Analyzer
# -------------------------
class MinuteAnalyzer:
    def __init__(self, key_openai: str = None, key_anthropic: str = None):
        self._ai_router = AIRouter(key_openai=key_openai, key_anthropic=key_anthropic); 

        system_prompt = 'あなたは自治体の議事録を分析する分析者です。'

        #message_list = []
        #message_list.append({'role': 'assistant', 'content': f'「暖かい」の対義語は？'})
        #print(ai_router.ask(system_prompt, message_list))

        self._schema = {
            "name": "topic_relevance_assessment_list",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["items"],
                "properties": {
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "topic",
                            "topic_relevance_score",
                            "topic_related_summary"
                        ],
                        "properties": {
                            "topic": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Topic name to assess (e.g., Urban Planning)"
                            },
                            "topic_relevance_score": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                            "description": "Relevance score indicating how strongly the minutes content relates to the topic (0-100)"
                            },
                            "topic_related_summary": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Summary of the content related to the topic"
                            }
                        }}
                    }
                }
            }
        }


        self._system_prompt = 'あなたは自治体の議事録を分析する分析者です。'
        
    def _build(self, text: str):
        user_prompt = f'''
        議事録の内容は下記のとおりです。
        ```txt
        {text}
        ```

        この議事録が何の議題に関するものなのかを調査してください。
        議題の対象は下記の通りです。

        ```
        - 年金
        - 税金
        - 少年犯罪
        ```

        議題との関連度合いは0～100の範囲で表現してください。

        '''
        return user_prompt
    

            
    
    def ask(self, text: str):
        ret = self._ai_router.ask_json(system_prompt=self._system_prompt, user_text=self._build(text), schema=self._schema)
        if not ret['ok']:
            return None
        return MinuteReport(
            provider=ret['provider'], 
            model=ret['model'], 
            usage=Usage(
                input_tokens=ret['usage']['input_tokens'], 
                output_tokens=ret['usage']['output_tokens'], 
                total_tokens=ret['usage']['total_tokens'], 
            ), 
            raw_text=ret['raw_text'], 
            items=[MinuteReportItem(
                topic=d['topic'], 
                topic_relevance_score=d['topic_relevance_score'], 
                topic_related_summary=d['topic_related_summary']
                ) for d in ret['json']['items']]
        )
        


text = "今日は市内の書店の万引き被害についての議論をします。"

minute_analyzer = MinuteAnalyzer()
print(minute_analyzer.ask(text))
