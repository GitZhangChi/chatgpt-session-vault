from copy import deepcopy

import pytest

CID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def conversation_data():
    messages = [
        ("user", "Help me plan a thoughtful weekend in Kyoto.\n\n也请保留中文内容。"),
        (
            "assistant",
            """## A slower kind of weekend

Start with a quiet morning walk, then leave room for discoveries.

| Day | Morning | Afternoon |
| --- | --- | --- |
| Saturday | Philosopher's Path | A small ceramics studio |
| Sunday | Garden and tea | Walk by the river |

### A little code to keep the plan

```python
from dataclasses import dataclass

@dataclass
class Weekend:
    city: str = "Kyoto"
    pace: str = "slow"

print(Weekend())
```

The familiar identity $e^{i\\pi}+1=0$ can be rendered inline.

$$
\\int_0^1 x^2\\,dx = \\frac{1}{3}
$$

ChatGPT math also works: \\(a^2+b^2=c^2\\).

> Leave some time unplanned. The best part may be what you didn't schedule.

中文段落：把值得留下的对话，存成可以随时翻阅的文档。
""",
        ),
        ("user", "Can you make a simple packing checklist?"),
        (
            "assistant",
            "## Pack light\n\n- Comfortable walking shoes\n- A refillable water bottle\n"
            "- A small notebook\n- Room for something unexpected",
        ),
    ]
    mapping = {"root": {"id": "root", "parent": None, "children": ["m0"], "message": None}}
    for i, (role, text) in enumerate(messages):
        mapping[f"m{i}"] = {
            "id": f"m{i}",
            "parent": "root" if i == 0 else f"m{i - 1}",
            "children": [f"m{i + 1}"] if i < len(messages) - 1 else [],
            "message": {
                "author": {"role": role},
                "content": {"content_type": "text", "parts": [text]},
                "create_time": 1793145600 + i,
                "status": "finished_successfully",
                "recipient": "all",
                "metadata": {},
            },
        }
    return {
        "id": CID,
        "title": "A weekend worth remembering",
        "create_time": 1793145600,
        "update_time": 1793145900,
        "current_node": "m3",
        "mapping": mapping,
    }


class FakeChatGPT:
    def __init__(self, data):
        self.data = data

    def list_conversations(self, limit=5, archived=False):
        titles = [
            "A weekend worth remembering",
            "Understanding the attention mechanism",
            "A recipe for a rainy afternoon",
            "Notes on building something small",
            "The books we keep",
        ]
        return [
            {
                "id": f"00000000-0000-4000-8000-{i + 1:012d}",
                "title": title,
                "updated_at": "2026-10-28T08:45:00+00:00",
                "created_at": "2026-10-27T00:00:00+00:00",
                "update_time": 1793145900 - i,
            }
            for i, title in enumerate(titles[:limit])
        ]

    def fetch_conversation(self, cid):
        data = deepcopy(self.data)
        data["id"] = cid
        return data
