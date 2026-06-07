from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path


CN_TZ = timezone(timedelta(hours=8))

FOCUS_TOPICS = [
    "新技术正在如何改变旧行业",
    "AI时代下传统产业的重新崛起",
    "城市、高铁、民航、人口流动背后的真实逻辑",
    "教育、考研、体制、国企、中科院等系统真实运行方式",
    "社会规则与人性规律",
    "东亚（中国、日本、韩国、台湾）隐藏的历史与产业联系",
    "互联网情绪背后真正的经济与社会变化",
    "年轻人生活方式变化",
    "消费升级与品牌分层",
    "表面看不懂，但背后很有意思的现象",
]

TOPIC_QUERY_SEEDS = {
    "新技术正在如何改变旧行业": [
        "AI 传统行业 真实变化",
        "AI 工厂 订单 供应链",
        "AI 客服 报价 老行业",
        "老行业 数字化 不是互联网",
    ],
    "AI时代下传统产业的重新崛起": [
        "传统产业 年轻人 重新崛起",
        "产业带 工厂 自有品牌",
        "县城工厂 AI 订单 供应链",
        "实体行业 数字化 真实变化",
    ],
    "城市、高铁、民航、人口流动背后的真实逻辑": [
        "城市流动 高铁 人口 真实原因",
        "民航 高铁 时间成本",
        "二线城市 一线项目 周末高铁",
        "高铁 城市流动 年轻人 周末",
    ],
    "教育、考研、体制、国企、中科院等系统真实运行方式": [
        "考研 调剂 导师 体制 真实",
        "国企 编制 年轻人 真实体验",
        "中科院 读研 真实经历",
        "体制内 年轻人 真实感受",
    ],
    "社会规则与人性规律": [
        "社会规则 人情 真实逻辑",
        "熟人社会 风险 责任 背后逻辑",
        "人情世故 年轻人 边界感",
        "为什么很多事 先看谁承担风险",
    ],
    "东亚（中国、日本、韩国、台湾）隐藏的历史与产业联系": [
        "日本 韩国 台湾 中国 产业链 历史",
        "东亚 供应链 品牌 代工",
        "半导体 东亚 产业联系",
        "中日韩 产业链 隐藏联系",
    ],
    "互联网情绪背后真正的经济与社会变化": [
        "互联网情绪 经济变化",
        "就业焦虑 消费 情绪 背后逻辑",
        "年轻人 工资 房租 消费 真实",
        "网上情绪 背后 经济变化",
    ],
    "年轻人生活方式变化": [
        "年轻人 生活方式 变了 为什么",
        "返乡 县城 年轻人 生活方式",
        "搭子 副业 租房 年轻人",
        "年轻人 可撤退生活",
    ],
    "消费升级与品牌分层": [
        "消费降级 品牌分层 平替",
        "消费升级 渠道 价格差",
        "国货 供应链 品牌溢价",
        "品牌分层 年轻人 消费",
    ],
    "表面看不懂，但背后很有意思的现象": [
        "表面看不懂 背后逻辑",
        "反常识 真实经历 背后逻辑",
        "最近出现 奇怪现象 为什么",
        "小现象 背后 产业逻辑",
    ],
}

TOPIC_ALIASES = {
    "技术": "新技术正在如何改变旧行业",
    "ai": "新技术正在如何改变旧行业",
    "传统": "AI时代下传统产业的重新崛起",
    "产业": "AI时代下传统产业的重新崛起",
    "城市": "城市、高铁、民航、人口流动背后的真实逻辑",
    "高铁": "城市、高铁、民航、人口流动背后的真实逻辑",
    "民航": "城市、高铁、民航、人口流动背后的真实逻辑",
    "人口": "城市、高铁、民航、人口流动背后的真实逻辑",
    "教育": "教育、考研、体制、国企、中科院等系统真实运行方式",
    "考研": "教育、考研、体制、国企、中科院等系统真实运行方式",
    "体制": "教育、考研、体制、国企、中科院等系统真实运行方式",
    "国企": "教育、考研、体制、国企、中科院等系统真实运行方式",
    "中科院": "教育、考研、体制、国企、中科院等系统真实运行方式",
    "规则": "社会规则与人性规律",
    "人性": "社会规则与人性规律",
    "社会": "社会规则与人性规律",
    "东亚": "东亚（中国、日本、韩国、台湾）隐藏的历史与产业联系",
    "日本": "东亚（中国、日本、韩国、台湾）隐藏的历史与产业联系",
    "韩国": "东亚（中国、日本、韩国、台湾）隐藏的历史与产业联系",
    "台湾": "东亚（中国、日本、韩国、台湾）隐藏的历史与产业联系",
    "情绪": "互联网情绪背后真正的经济与社会变化",
    "互联网": "互联网情绪背后真正的经济与社会变化",
    "年轻人": "年轻人生活方式变化",
    "生活": "年轻人生活方式变化",
    "消费": "消费升级与品牌分层",
    "品牌": "消费升级与品牌分层",
    "现象": "表面看不懂，但背后很有意思的现象",
    "反常识": "表面看不懂，但背后很有意思的现象",
}

OUTPUT_SCHEMA = [
    "现象",
    "表面看起来是什么",
    "背后真实逻辑",
    "为什么最近开始出现",
    "它反映了什么趋势",
    "是否可能被夸大",
    "值不值得继续深挖",
]

DEFAULT_QUERY = "AI时代 传统产业 城市流动 教育体制 年轻人消费 信息差 真实逻辑"


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        clean = " ".join((item or "").split())
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def split_terms(query: str) -> list[str]:
    terms = re.split(r"[\s,，、/|;；]+", query or "")
    return dedupe([term for term in terms if len(term.strip()) >= 2])


def focused_queries_for_term(term: str) -> list[str]:
    if re.search(r"AI|人工智能|技术|自动化", term, re.I):
        return [
            "AI 传统行业 真实变化",
            "AI 工厂 订单 供应链",
            "AI 客服 报价 老行业",
        ]
    if any(word in term for word in ["传统", "产业", "工厂", "制造", "实体"]):
        return [
            "传统产业 年轻人 重新崛起",
            "产业带 工厂 自有品牌",
            "老行业 数字化 不是互联网",
        ]
    if any(word in term for word in ["城市", "高铁", "民航", "人口", "流动"]):
        return [
            "城市流动 高铁 人口 真实原因",
            "民航 高铁 时间成本",
            "二线城市 一线项目 周末高铁",
        ]
    if any(word in term for word in ["教育", "考研", "体制", "国企", "中科院", "编制"]):
        return [
            "考研 调剂 导师 体制 真实",
            "国企 编制 年轻人 真实体验",
            "中科院 读研 真实经历",
        ]
    if any(word in term for word in ["消费", "品牌", "平替", "升级", "降级"]):
        return [
            "消费降级 品牌分层 平替",
            "消费升级 渠道 价格差",
            "国货 供应链 品牌溢价",
        ]
    if any(word in term for word in ["年轻人", "生活", "方式", "返乡", "搭子"]):
        return [
            "年轻人 生活方式 变了 为什么",
            "返乡 县城 年轻人 生活方式",
            "搭子 副业 租房 年轻人",
        ]
    if any(word in term for word in ["情绪", "互联网", "经济", "就业", "工资"]):
        return [
            "互联网情绪 经济变化",
            "就业焦虑 消费 情绪 背后逻辑",
            "年轻人 工资 房租 消费 真实",
        ]
    if any(word in term for word in ["东亚", "日本", "韩国", "台湾", "历史"]):
        return [
            "日本 韩国 台湾 中国 产业链 历史",
            "东亚 供应链 品牌 代工",
            "半导体 东亚 产业联系",
        ]
    if any(word in term for word in ["规则", "人性", "社会", "关系"]):
        return [
            "社会规则 人情 真实逻辑",
            "熟人社会 风险 责任 背后逻辑",
            "人情世故 年轻人 边界感",
        ]
    return [
        f"{term} 信息差 真实经历",
        f"{term} 背后逻辑 为什么",
        f"{term} 真实经历 反常识",
    ]


def topic_for_term(term: str) -> str:
    if re.search(r"AI|人工智能|技术|自动化", term, re.I):
        return "新技术正在如何改变旧行业"
    if any(word in term for word in ["传统", "产业", "工厂", "制造", "实体"]):
        return "AI时代下传统产业的重新崛起"
    if any(word in term for word in ["城市", "高铁", "民航", "人口", "流动"]):
        return "城市、高铁、民航、人口流动背后的真实逻辑"
    if any(word in term for word in ["教育", "考研", "体制", "国企", "中科院", "编制"]):
        return "教育、考研、体制、国企、中科院等系统真实运行方式"
    if any(word in term for word in ["消费", "品牌", "平替", "升级", "降级"]):
        return "消费升级与品牌分层"
    if any(word in term for word in ["年轻人", "生活", "方式", "返乡", "搭子"]):
        return "年轻人生活方式变化"
    if any(word in term for word in ["情绪", "互联网", "经济", "就业", "工资"]):
        return "互联网情绪背后真正的经济与社会变化"
    if any(word in term for word in ["东亚", "日本", "韩国", "台湾", "历史"]):
        return "东亚（中国、日本、韩国、台湾）隐藏的历史与产业联系"
    if any(word in term for word in ["规则", "人性", "社会", "关系"]):
        return "社会规则与人性规律"
    return ""


def split_topic_tokens(selection: str) -> list[str]:
    if not selection:
        return []
    return dedupe([part for part in re.split(r"[\s,，、/|;；]+", selection) if part.strip()])


def select_focus_topics(selection: str, existing_topics: list[str] | None = None) -> tuple[list[str], list[str]]:
    if not selection:
        return existing_topics or FOCUS_TOPICS, []
    selected: list[str] = []
    warnings: list[str] = []
    for token in split_topic_tokens(selection):
        clean = token.strip()
        matched = ""
        if clean.isdigit():
            index = int(clean)
            if 1 <= index <= len(FOCUS_TOPICS):
                matched = FOCUS_TOPICS[index - 1]
        if not matched:
            lowered = clean.lower()
            matched = TOPIC_ALIASES.get(lowered, "")
        if not matched:
            for topic in FOCUS_TOPICS:
                if clean in topic or topic in clean:
                    matched = topic
                    break
        if matched:
            selected.append(matched)
        else:
            warnings.append(f"未识别关注主题：{clean}")
    selected = dedupe(selected)
    if not selected:
        warnings.append("未匹配到有效主题，已回退到全部主题。")
        selected = FOCUS_TOPICS
    return selected, warnings


def topic_seed_queries(focus_topics: list[str]) -> list[str]:
    seeds: list[str] = []
    for topic in focus_topics:
        seeds.extend(TOPIC_QUERY_SEEDS.get(topic, []))
    return seeds


def expand_queries(query: str, limit: int = 24, focus_topics: list[str] | None = None) -> list[str]:
    base = " ".join((query or DEFAULT_QUERY).split())
    terms = split_terms(base)
    seeds: list[str] = []
    selected_topics = focus_topics or FOCUS_TOPICS
    all_topics_selected = len(selected_topics) == len(FOCUS_TOPICS)

    if not all_topics_selected:
        seeds.extend(topic_seed_queries(selected_topics))
        for term in terms:
            if topic_for_term(term) in selected_topics:
                seeds.extend(focused_queries_for_term(term))
        return dedupe(seeds)[:limit]

    if len(terms) <= 3:
        seeds.extend(
            [
                base,
                f"{base} 信息差 真实经历",
                f"{base} 背后逻辑 为什么",
                f"{base} 真实经历 反常识",
                f"{base} 普通人感受 变化",
            ]
        )
    else:
        for term in terms:
            seeds.extend(focused_queries_for_term(term))

    seeds.extend(topic_seed_queries(selected_topics))
    return dedupe(seeds)[:limit]


def load_json(path: str | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a canonical XHS trend-observer request.")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--topic", default="")
    parser.add_argument("--request-json", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--max-notes", type=int, default=60)
    parser.add_argument("--per-query", type=int, default=10)
    parser.add_argument("--max-comments", type=int, default=30)
    parser.add_argument("--focus-topics", default="")
    parser.add_argument("--query-limit", type=int, default=24)
    parser.add_argument("--list-focus-topics", action="store_true")
    args = parser.parse_args()

    if args.list_focus_topics:
        for index, topic in enumerate(FOCUS_TOPICS, start=1):
            print(f"{index}. {topic}")
        return
    if not args.out:
        raise SystemExit("--out is required unless --list-focus-topics is used")

    now = datetime.now(CN_TZ).isoformat(timespec="seconds")
    existing = load_json(args.request_json)
    query = existing.get("query") or args.query or DEFAULT_QUERY
    topic = args.topic or existing.get("meta", {}).get("topic") or query
    existing_focus_topics = existing.get("focus_topics") if isinstance(existing.get("focus_topics"), list) else None
    focus_topics, focus_warnings = select_focus_topics(args.focus_topics, existing_focus_topics)

    request = {
        "meta": {
            "topic": topic,
            "created_at": now,
            "source": "xiaohongshu",
            "tool": "trend_xhs_observer",
            "purpose": "寻找能解释真实运行逻辑、带来认知冲击的信息差内容",
            "focus_topic_selection": args.focus_topics,
            "focus_topic_warnings": focus_warnings,
        },
        "query": query,
        "expanded_queries": existing.get("expanded_queries") or expand_queries(query, args.query_limit, focus_topics),
        "focus_topics": focus_topics,
        "output_schema": existing.get("output_schema") or OUTPUT_SCHEMA,
        "constraints": {
            "avoid_hot_only": True,
            "prioritize_comments": True,
            "prioritize_information_gap": True,
            "explain_why_not_just_summarize": True,
            "max_notes": args.max_notes,
            "per_query": args.per_query,
            "max_comments": args.max_comments,
            "query_limit": args.query_limit,
        },
    }

    if existing:
        request["original_request"] = existing

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"request written: {out}")


if __name__ == "__main__":
    main()
