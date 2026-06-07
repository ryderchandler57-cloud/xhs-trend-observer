from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from source_urls import select_report_source_url


CN_TZ = timezone(timedelta(hours=8))

THEME_KEYWORDS = {
    "技术改造旧行业": ["AI", "人工智能", "自动化", "机器人", "数字化", "模型", "算法", "SaaS", "低代码", "工厂", "农业", "制造", "客服", "运营"],
    "传统产业再崛起": ["传统行业", "老行业", "制造业", "外贸", "工厂", "线下", "实体", "供应链", "订单", "批发", "产业带", "县城"],
    "城市与交通流动": ["城市", "高铁", "民航", "机场", "通勤", "地铁", "人口", "迁移", "返乡", "周末", "省会", "二线", "县城"],
    "教育与组织系统": ["考研", "调剂", "导师", "中科院", "国企", "央企", "体制", "编制", "高校", "绩效", "职称", "科研", "学校"],
    "社会规则与人性": ["人情", "关系", "规则", "潜规则", "饭局", "面子", "情绪", "博弈", "普通人", "资源", "门槛"],
    "东亚产业历史联系": ["日本", "韩国", "台湾", "东亚", "半导体", "代工", "供应链", "外贸", "历史", "产业转移", "品牌"],
    "互联网情绪与经济变化": ["热搜", "吐槽", "打工人", "失业", "裁员", "工资", "消费降级", "焦虑", "房价", "就业", "平台"],
    "年轻人生活方式": ["年轻人", "00后", "95后", "租房", "周末", "副业", "躺平", "搭子", "通勤", "生活方式", "返乡"],
    "消费与品牌分层": ["消费", "品牌", "平替", "国货", "奢侈", "性价比", "折扣", "会员", "渠道", "下沉", "溢价"],
    "反常识现象": ["为什么", "看不懂", "反常识", "居然", "突然", "表面", "背后", "信息差", "原来如此", "不理解"],
}

SIGNAL_WORDS = [
    "涨价",
    "降价",
    "排队",
    "预约",
    "名额",
    "订单",
    "工资",
    "岗位",
    "客户",
    "利润",
    "成本",
    "流量",
    "供应链",
    "政策",
    "补贴",
    "高铁",
    "机场",
    "导师",
    "编制",
    "调剂",
    "算法",
    "模型",
    "自动化",
    "库存",
    "渠道",
    "县城",
    "下沉",
    "外贸",
    "代工",
]

COMMENT_SIGNAL_WORDS = [
    "我这边",
    "今年",
    "最近",
    "现在",
    "去年",
    "已经",
    "涨",
    "降",
    "贵",
    "便宜",
    "排队",
    "预约",
    "名额",
    "工资",
    "订单",
    "客户",
    "岗位",
    "通勤",
    "高铁",
    "机场",
    "调剂",
    "导师",
    "国企",
    "编制",
    "工厂",
    "供应链",
    "渠道",
    "别信",
    "确实",
    "不是",
]

NOISE_COMMENT_WORDS = ["蹲", "插眼", "mark", "马克", "求链接", "好看", "哈哈", "收藏了", "学习了"]
AD_WORDS = ["私信", "加我", "合作", "团购", "优惠券", "VX", "微信", "带货", "种草", "购买链接"]
QUESTION_WORDS = ["求问", "有没有", "怎么选", "能买吗", "靠谱吗", "要不要"]

LOGIC_TEMPLATES = {
    "技术改造旧行业": {
        "logic": "真正变化往往不在“AI替代整个行业”，而在获客、客服、排产、培训、质检、报价等局部环节被软件化。旧行业的利润薄、流程长，所以只要某个环节降本明显，就会迅速改变从业者的分工和议价能力。",
        "recent": "最近开始明显，是因为模型工具、低代码工具和自动化设备的试错成本下降，同时人工、流量和交付压力都在上升，旧行业终于有动力把过去靠经验和人情处理的环节拆出来。",
        "trend": "技术红利正在从互联网公司外溢到县域、工厂、门店和服务业，传统行业不会消失，但行业里的岗位结构和利润分配会变。",
    },
    "传统产业再崛起": {
        "logic": "表面是老行业又热了，背后是线上流量贵、就业不确定、供应链成熟和低成本工具扩散之后，实体产业的现金流、可控性和长期壁垒重新被看见。",
        "recent": "最近出现，是因为互联网增长放缓后，年轻人和创业者开始寻找更接近真实需求的生意；同时平台把产业带、工厂和批发渠道展示出来，信息差被重新定价。",
        "trend": "传统产业不是简单回潮，而是在用平台化、数字化和更细的品牌分层重组。",
    },
    "城市与交通流动": {
        "logic": "城市流动不是单纯由“喜欢哪里”决定，而是由就业机会、通勤时间、票价、教育医疗资源、房租和枢纽密度共同决定。高铁和民航改变的是时间成本，也改变了城市之间的心理距离。",
        "recent": "最近更显眼，是因为高铁网络成熟、远程办公和周末短途消费普及，同时一线城市成本压力让人更频繁比较城市性价比。",
        "trend": "人口和消费正在沿交通枢纽、区域中心和可负担生活半径重新分布。",
    },
    "教育与组织系统": {
        "logic": "系统并不只按个人努力运行，还按名额、预算、导师资源、评价指标、风险控制和组织惯性运行。很多看似个人选择的问题，本质是制度容量和激励结构的问题。",
        "recent": "最近被更多人讨论，是因为升学和就业压力叠加，体制内外的收益差、稳定性和机会成本都变得更敏感，评论区也更容易暴露内部规则。",
        "trend": "年轻人正在从“只看光环”转向理解系统约束，教育和组织选择会越来越像风险管理。",
    },
    "社会规则与人性": {
        "logic": "很多社会规则表面是礼貌、关系或情绪，底层其实是在分配风险、信任和资源。人们不是总按公开规则行动，而是按成本、面子、可退出性和长期关系行动。",
        "recent": "最近出现更多，是因为经济压力让隐性规则的成本变高，过去被默认为“人情世故”的部分开始被年轻人公开讨论。",
        "trend": "社会协作正在从熟人默契转向更明确的边界、合同和可验证信息。",
    },
    "东亚产业历史联系": {
        "logic": "东亚产业关系经常被民族情绪遮住，实际运行更像一张长期分工网络：技术、资本、代工、渠道和品牌在不同阶段跨地区转移，竞争和依赖同时存在。",
        "recent": "最近更容易被看见，是因为半导体、新能源、消费电子和国货品牌让产业链位置成为大众话题，平台内容把历史脉络和现实消费连接起来。",
        "trend": "东亚竞争会更激烈，但供应链层面的互相嵌套不会立刻消失。",
    },
    "互联网情绪与经济变化": {
        "logic": "互联网情绪往往不是凭空来的，它通常对应收入预期、就业安全、资产价格、行业利润或身份通道的变化。表层是吐槽，底层是经济约束重新压到个人生活。",
        "recent": "最近变多，是因为就业、房租、消费预算和行业收缩被普通人同时感受到，平台又把分散体验放大成共同情绪。",
        "trend": "未来很多公共讨论会继续从情绪入口爆发，但判断价值在于找到背后的现金流和制度变化。",
    },
    "年轻人生活方式": {
        "logic": "生活方式变化不是简单叛逆，而是预算、城市成本、就业弹性、社交半径和平台推荐共同塑造的结果。年轻人会把低成本、可复制、可退出的选择当成新的安全感。",
        "recent": "最近明显，是因为收入预期下降和城市生活成本上升，同时高铁、外卖、二手平台、远程工具让替代生活方案更容易执行。",
        "trend": "消费、居住、社交和职业将更分散，传统单一路径的吸引力下降。",
    },
    "消费与品牌分层": {
        "logic": "消费分层的核心不是大家都买便宜，而是不同人对功能、身份、渠道、情绪价值和售后保障的支付意愿分开了。品牌溢价被拆解，平替和高端会同时存在。",
        "recent": "最近突出，是因为预算收紧、渠道透明、国货成熟和内容平台测评让消费者更容易比较真实成本。",
        "trend": "品牌竞争会从单纯讲故事转向供应链、渠道、服务和社群的综合分层。",
    },
    "反常识现象": {
        "logic": "看不懂的现象通常不是无规律，而是我们只看见了表层标签，没有看见谁在承担成本、谁获得收益、谁拥有信息优势，以及规则改变后谁被迫重新选择。",
        "recent": "最近被注意到，是因为平台把局部真实经验聚合起来，一些原来只在行业内部流通的信息开始外溢。",
        "trend": "信息差会继续从封闭行业流向大众平台，但噪音也会同步增加。",
    },
}


def load_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_json(path: str, data: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def text_blob(record: dict[str, Any]) -> str:
    pieces = [
        record.get("title", ""),
        record.get("card_text", ""),
        record.get("text", ""),
        " ".join(record.get("comments", []) or []),
    ]
    return "\n".join(piece for piece in pieces if piece)


def brief(text: str, limit: int = 120) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def useful_comments(comments: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for comment in comments or []:
        clean = re.sub(r"\s+", " ", comment).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        lower = clean.lower()
        if any(noise.lower() in lower for noise in NOISE_COMMENT_WORDS):
            continue
        if len(clean) < 8 or len(clean) > 180:
            continue
        has_number = bool(re.search(r"\d", clean))
        has_signal = any(word in clean for word in COMMENT_SIGNAL_WORDS)
        has_negation = any(word in clean for word in ["不一定", "别信", "并不是", "反而", "坑", "踩雷"])
        if has_number or has_signal or has_negation:
            out.append(clean)
    return out[:8]


def score_comment(comment: str, topic_blob: str = "") -> dict[str, Any]:
    clean = re.sub(r"\s+", " ", comment or "").strip()
    lower = clean.lower()
    score = 0
    tags: list[str] = []
    reasons: list[str] = []

    if not clean:
        return {"comment": "", "value_score": 0, "reason": "空评论", "tags": []}
    if len(clean) >= 10:
        score += 1
    if any(word.lower() in lower for word in NOISE_COMMENT_WORDS) or len(clean) < 6:
        score -= 3
        reasons.append("短句、水评或无信息量")
    if any(word in clean for word in ["我", "我们", "我这边", "身边", "公司", "学校", "工厂", "项目", "客户"]):
        score += 2
        tags.append("真实体验")
        reasons.append("包含个人或一线经验")
    if re.search(r"\d+(?:\.\d+)?\s*(?:元|块|万|千|k|K|%|公里|小时|天|年|个月|人|次)?", clean):
        score += 2
        tags.append("价格")
        reasons.append("有数字/价格/时间等可核验信息")
    if any(word in clean for word in ["坑", "避雷", "踩雷", "别信", "不推荐", "风险", "注意", "谨慎"]):
        score += 2
        tags.append("避坑")
        reasons.append("提供避坑或风险提示")
    if any(word in clean for word in ["怎么", "流程", "步骤", "教程", "申请", "路线", "入口", "材料", "预约"]):
        score += 2
        tags.append("教程")
        reasons.append("包含做法或流程信息")
    if any(word in clean for word in ["对比", "相比", "不如", "更适合", "差别", "区别", "争议", "反而"]):
        score += 1
        tags.append("争议观点")
        reasons.append("包含比较或反向观点")
    if any(word in clean for word in ["最近", "今年", "去年", "现在", "周末", "暑假", "旺季", "淡季"]):
        score += 1
        tags.append("补充信息")
        reasons.append("包含时效信息")

    topic_terms = [term for term in re.split(r"\s+", topic_blob) if len(term) >= 2]
    if topic_terms and any(term in clean for term in topic_terms[:12]):
        score += 1
        reasons.append("和搜索主题相关")

    score = max(0, min(10, score))
    unique_tags = list(dict.fromkeys(tags))
    if score >= 6 and not unique_tags:
        unique_tags.append("补充信息")
    return {
        "comment": clean,
        "value_score": score,
        "reason": "；".join(reasons[:4]) if reasons else "信息量有限",
        "tags": unique_tags,
    }


def valuable_comments(comments: list[str], topic_blob: str) -> list[dict[str, Any]]:
    scored = [score_comment(comment, topic_blob) for comment in comments or []]
    scored = [item for item in scored if item["comment"]]
    scored.sort(key=lambda item: (item["value_score"], len(item["comment"])), reverse=True)
    return scored[:12]


def classify_theme(blob: str) -> str:
    lowered = blob.lower()
    tech_words = ["ai", "人工智能", "自动化", "机器人", "模型", "算法", "知识库", "传感器", "数字化"]
    old_industry_words = ["工厂", "农业", "汽修", "客服", "制造", "包装", "维修", "报价", "门店", "服务行业", "传统行业", "老师傅"]
    if any(word in lowered for word in tech_words) and any(word in blob for word in old_industry_words):
        return "技术改造旧行业"

    scores: Counter[str] = Counter()
    for theme, keywords in THEME_KEYWORDS.items():
        for keyword in keywords:
            if keyword and keyword.lower() in lowered:
                scores[theme] += 1
    if not scores:
        return "反常识现象"
    return scores.most_common(1)[0][0]


def extract_signals(blob: str) -> list[str]:
    signals = [word for word in SIGNAL_WORDS if word.lower() in blob.lower()]
    numbers = re.findall(r"\d+(?:\.\d+)?(?:万|千|k|K|元|块|年|个月|天|小时|公里|%|人|次)?", blob)
    for number in numbers[:8]:
        signals.append(number)
    return list(dict.fromkeys(signals))[:12]


def engagement_score(record: dict[str, Any]) -> int:
    total = 0
    for key in ["like_count", "collect_count", "comment_count"]:
        value = record.get(key)
        if isinstance(value, int) and value > 0:
            total += min(8, int(math.log10(value + 1) * 2))
    return total


def score_record(record: dict[str, Any], comments: list[str], blob: str) -> tuple[int, list[str], list[str]]:
    reasons: list[str] = []
    noise: list[str] = []
    score = 8
    text_len = len(record.get("text", "") or "")
    if text_len >= 500:
        score += 22
        reasons.append("正文信息量较高")
    elif text_len >= 160:
        score += 14
        reasons.append("正文有一定细节")
    elif text_len < 30:
        score -= 18
        noise.append("正文过短或只有标题")

    if comments:
        add = min(28, len(comments) * 7)
        score += add
        reasons.append(f"评论区有{len(comments)}条实用补充")

    signals = extract_signals(blob)
    if signals:
        score += min(18, len(signals) * 3)
        reasons.append("包含产业/制度/价格/流动等信号")

    if re.search(r"\d", blob):
        score += 6
        reasons.append("出现可核验数字")

    if any(word in blob for word in ["为什么", "背后", "原来", "信息差", "真实", "看不懂", "反常识"]):
        score += 8
        reasons.append("有解释型或反常识表达")

    score += engagement_score(record)

    if any(word in blob for word in AD_WORDS):
        score -= 20
        noise.append("疑似广告或引流口吻")
    if any(word in (record.get("title", "") or "") for word in QUESTION_WORDS):
        score -= 10
        noise.append("提问帖倾向，需二次核验")
    if record.get("extraction_method") == "search-card fallback":
        score -= 8
        noise.append("只抓到搜索卡片，缺少详情页")

    score = max(0, min(100, score))
    return score, reasons, noise


def title_to_phenomenon(title: str, theme: str, signals: list[str]) -> str:
    title = brief(title, 42)
    if title and len(title) >= 6:
        return title
    if signals:
        return f"{theme}里的“{'、'.join(signals[:3])}”信号"
    return f"{theme}中的一个待解释现象"


def surface_text(record: dict[str, Any], theme: str) -> str:
    title = record.get("title") or "这类讨论"
    return f"表面看像是平台上的一条经验帖或吐槽：{brief(title, 70)}。如果只看标题，它容易被理解成个体经历、情绪表达或单点案例。"


def overstated_text(score: int, comments: list[str], source_count: int) -> str:
    if score < 45:
        return "可能被明显夸大。目前证据偏弱，尤其需要防止把单条经历当作普遍规律。"
    if source_count <= 1 and len(comments) < 2:
        return "有被夸大的风险。它至少说明了一个局部信号，但还缺少跨地区、跨人群或跨时间的验证。"
    return "仍可能被平台样本放大，但正文和评论区已经提供了一些相互印证的细节，可以作为继续观察的线索。"


def deeper_text(score: int, theme: str, signals: list[str]) -> str:
    if score >= 70:
        return f"值得继续深挖。建议沿着“{theme}”方向查更多行业数据、政策变化和评论区反例，重点验证：{('、'.join(signals[:5]) if signals else '成本、收益、规则和人群变化')}。"
    if score >= 50:
        return f"中等值得。它像一个早期信号，适合再搜相邻关键词和反面案例，避免被单一叙事带偏。"
    return "暂时只适合作为待核实线索。先看是否有更多具体评论、时间线或现实数据支撑。"


def make_observation(record: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    blob = text_blob(record)
    topic_blob = " ".join([request.get("query", ""), request.get("meta", {}).get("topic", ""), record.get("query", "")])
    scored_comments = valuable_comments(record.get("comments", []) or [], topic_blob)
    comments = [item["comment"] for item in scored_comments if item["value_score"] >= 4]
    if not comments:
        comments = useful_comments(record.get("comments", []) or [])
    theme = classify_theme(blob)
    signals = extract_signals(blob)
    score, reasons, noise = score_record(record, comments, blob)
    template = LOGIC_TEMPLATES.get(theme, LOGIC_TEMPLATES["反常识现象"])
    confidence = "high" if score >= 72 and comments else "medium" if score >= 48 else "low"
    evidence: list[dict[str, str]] = []
    if record.get("text"):
        evidence.append({"type": "正文线索", "text": brief(record["text"], 110)})
    elif record.get("card_text"):
        evidence.append({"type": "搜索卡片线索", "text": brief(record["card_text"], 110)})
    for comment in comments[:4]:
        evidence.append({"type": "评论区补充", "text": brief(comment, 90)})
    report_url, report_url_type, source_url_valid = select_report_source_url(record)

    return {
        "phenomenon": title_to_phenomenon(record.get("title", ""), theme, signals),
        "surface_appearance": surface_text(record, theme),
        "underlying_logic": template["logic"],
        "why_recent": template["recent"],
        "trend_reflected": template["trend"],
        "possibly_overstated": overstated_text(score, comments, 1),
        "worth_deeper": deeper_text(score, theme, signals),
        "theme": theme,
        "signals": signals,
        "research_score": score,
        "value_level": "high" if score >= 72 else "medium" if score >= 48 else "low",
        "value_reasons": reasons,
        "noise_reasons": noise,
        "confidence": confidence,
        "source": {
            "title": record.get("title", ""),
            "note_id": record.get("note_id") or record.get("id", ""),
            "url": report_url,
            "raw_url": record.get("url", ""),
            "report_source_url": report_url,
            "report_source_url_type": report_url_type,
            "source_url_valid": source_url_valid,
            "query": record.get("query", ""),
            "author": record.get("author", ""),
            "created_at": record.get("created_at", ""),
            "like_count": record.get("like_count"),
            "collect_count": record.get("collect_count"),
            "comment_count": record.get("comment_count"),
            "extraction_method": record.get("extraction_method", ""),
            "detail_attempted": record.get("detail_attempted", False),
            "detail_opened": record.get("detail_opened", False),
            "detail_url": record.get("detail_url", ""),
            "open_strategy": record.get("open_strategy", ""),
            "api_payload_count": record.get("api_payload_count", 0),
            "api_fetch_attempted": record.get("api_fetch_attempted", False),
            "api_detail_fetch_count": record.get("api_detail_fetch_count", 0),
            "api_comment_count": record.get("api_comment_count", 0),
            "dom_comment_count": record.get("dom_comment_count", 0),
            "comment_source": record.get("comment_source", ""),
        },
        "evidence": evidence,
        "useful_comments": comments,
        "valuable_comments": scored_comments,
    }


def theme_summary(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in observations:
        grouped[item["theme"]].append(item)
    summaries = []
    for theme, items in grouped.items():
        signals: Counter[str] = Counter()
        for item in items:
            signals.update(item.get("signals", []))
        avg_score = round(sum(item["research_score"] for item in items) / max(1, len(items)), 1)
        summaries.append(
            {
                "theme": theme,
                "count": len(items),
                "avg_score": avg_score,
                "top_signals": [signal for signal, _ in signals.most_common(8)],
            }
        )
    return sorted(summaries, key=lambda row: (row["count"], row["avg_score"]), reverse=True)


def structured_note(record: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    topic_blob = " ".join([request.get("query", ""), request.get("meta", {}).get("topic", ""), record.get("query", "")])
    scored = valuable_comments(record.get("comments", []) or [], topic_blob)
    report_url, report_url_type, source_url_valid = select_report_source_url(record)
    return {
        "keyword": record.get("keyword") or record.get("query", ""),
        "note_id": record.get("note_id") or record.get("id", ""),
        "title": record.get("title", ""),
        "content": record.get("content") or record.get("text", ""),
        "author": record.get("author", ""),
        "publish_time": record.get("publish_time") or record.get("created_at", ""),
        "like_count": record.get("like_count"),
        "collect_count": record.get("collect_count"),
        "comment_count": record.get("comment_count"),
        "comments": record.get("comments", []) or [],
        "valuable_comments": scored,
        "images": record.get("images", []) or [],
        "video": record.get("video", ""),
        "url": report_url,
        "raw_url": record.get("url", ""),
        "report_source_url": report_url,
        "report_source_url_type": report_url_type,
        "source_url_valid": source_url_valid,
        "access_status": record.get("access_status", ""),
        "extraction_method": record.get("extraction_method", ""),
    }


def build_ai_summary(observations: list[dict[str, Any]], notes: list[dict[str, Any]]) -> dict[str, Any]:
    theme_counter = Counter(item.get("theme", "") for item in observations if item.get("theme"))
    signal_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    comment_texts: list[str] = []
    for item in observations:
        signal_counter.update(item.get("signals", []) or [])
        for comment in item.get("valuable_comments", []) or []:
            if comment.get("value_score", 0) >= 5:
                comment_texts.append(comment.get("comment", ""))
                tag_counter.update(comment.get("tags", []) or [])

    high = observations[:8]
    top_themes = [theme for theme, _ in theme_counter.most_common(5)]
    top_signals = [signal for signal, _ in signal_counter.most_common(12)]
    top_tags = [tag for tag, _ in tag_counter.most_common(8)]
    pain_keywords = [word for word in top_signals if word in {"成本", "工资", "订单", "岗位", "通勤", "调剂", "导师", "渠道", "库存", "客户", "预约", "排队"}]

    return {
        "high_frequency_views": [
            f"{theme} 相关内容出现 {count} 次，值得作为主线观察。"
            for theme, count in theme_counter.most_common(5)
        ],
        "user_pain_points": [
            f"评论和正文反复出现“{word}”，说明它可能是用户真实决策成本。"
            for word in pain_keywords[:6]
        ] or ["样本中的痛点信号还不够集中，需要扩大采集量或补充评论证据。"],
        "recommend_reasons": [
            brief(item.get("underlying_logic", ""), 120)
            for item in high[:4]
            if item.get("value_level") in {"high", "medium"}
        ],
        "avoid_pitfalls": [
            comment
            for comment in comment_texts
            if any(word in comment for word in ["坑", "避雷", "别信", "不推荐", "风险", "注意", "谨慎"])
        ][:8],
        "keyword_trends": top_signals[:10],
        "trend_tags": top_themes + top_tags,
        "actionable_suggestions": [
            "优先深挖有正文和高价值评论共同支撑的笔记，避免只根据标题判断趋势。",
            "把出现数字、价格、时间、岗位、地点和对比的信息单独核验。",
            "对争议观点做二次搜索：看是否跨作者、跨评论区重复出现。",
            "把搜索结果按综合、最新、热门各跑一轮，区分长期结构变化和短期情绪波动。",
        ],
        "content_conclusions": [
            brief(item.get("phenomenon", ""), 90)
            for item in high[:6]
        ],
        "kol_analysis": build_kol_analysis(notes),
        "comment_sentiment": build_comment_sentiment(notes),
        "viral_note_teardown": build_viral_note_teardown(observations, notes),
        "competitor_comparison": build_competitor_comparison(notes),
    }


def build_kol_analysis(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for note in notes:
        author = note.get("author") or "未知作者"
        grouped[author].append(note)
    rows: list[dict[str, Any]] = []
    for author, items in grouped.items():
        if author == "未知作者":
            continue
        total_likes = sum((item.get("like_count") or 0) for item in items if isinstance(item.get("like_count"), int))
        total_comments = sum((item.get("comment_count") or 0) for item in items if isinstance(item.get("comment_count"), int))
        rows.append(
            {
                "author": author,
                "note_count": len(items),
                "total_likes": total_likes,
                "total_comments": total_comments,
                "main_topics": list(dict.fromkeys((item.get("keyword") or "") for item in items if item.get("keyword")))[:5],
            }
        )
    rows.sort(key=lambda row: (row["note_count"], row["total_likes"], row["total_comments"]), reverse=True)
    return rows[:10]


def build_comment_sentiment(notes: list[dict[str, Any]]) -> dict[str, Any]:
    positive_words = ["推荐", "有用", "确实", "省", "快", "值得", "稳定", "方便", "成交率", "覆盖"]
    negative_words = ["坑", "踩雷", "别信", "风险", "贵", "难", "卡", "压价", "不推荐", "不可控"]
    counts = {"positive": 0, "negative": 0, "neutral": 0}
    examples = {"positive": [], "negative": [], "neutral": []}
    for note in notes:
        for comment in note.get("valuable_comments", []) or []:
            text = comment.get("comment", "")
            neg = any(word in text for word in negative_words)
            pos = any(word in text for word in positive_words)
            bucket = "negative" if neg and not pos else "positive" if pos and not neg else "neutral"
            counts[bucket] += 1
            if len(examples[bucket]) < 5:
                examples[bucket].append(text)
    total = sum(counts.values()) or 1
    return {
        "counts": counts,
        "ratio": {key: round(value / total, 3) for key, value in counts.items()},
        "examples": examples,
    }


def build_viral_note_teardown(observations: list[dict[str, Any]], notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    note_by_id = {note.get("note_id"): note for note in notes}
    rows: list[dict[str, Any]] = []
    for item in observations[:12]:
        source = item.get("source", {}) or {}
        note_id = source.get("note_id") or source.get("url", "").rstrip("/").split("/")[-1]
        note = note_by_id.get(note_id, {})
        title = source.get("title") or note.get("title") or item.get("phenomenon", "")
        hooks = []
        if any(word in title for word in ["为什么", "不是", "突然", "背后", "原来", "越来越"]):
            hooks.append("反常识/解释型标题")
        if item.get("signals"):
            hooks.append("包含可讨论的现实信号")
        if item.get("valuable_comments"):
            hooks.append("评论区可补充二次观点")
        if note.get("like_count") or note.get("comment_count"):
            hooks.append("互动数据可作为弱热度参考")
        rows.append(
            {
                "title": title,
                "score": item.get("research_score", 0),
                "hooks": hooks or ["结构信息不足"],
                "suggested_rewrite": f"从“{brief(title, 36)}”延展成：现象是什么、为什么最近出现、谁在承担成本。",
                "url": source.get("url") or note.get("url", ""),
            }
        )
    return rows[:8]


def build_competitor_comparison(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    brand_pattern = re.compile(r"[A-Za-z][A-Za-z0-9+._-]{1,}|[\u4e00-\u9fff]{2,}(?:品牌|国货|平替|渠道|直播间|奥莱|工厂|供应链)")
    rows: list[dict[str, Any]] = []
    for note in notes:
        text = " ".join([note.get("title", ""), note.get("content", ""), " ".join(note.get("comments", [])[:4])])
        hits = list(dict.fromkeys(match.group(0) for match in brand_pattern.finditer(text)))
        if len(hits) < 2:
            continue
        rows.append(
            {
                "note_title": note.get("title", ""),
                "entities": hits[:6],
                "comparison_hint": "同一笔记里出现多个品牌/渠道/供应链实体，可作为竞品或渠道对比线索。",
                "url": note.get("url", ""),
            }
        )
    return rows[:10]


def normalize(request: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    leads: list[dict[str, Any]] = []
    dropped_noise: list[dict[str, Any]] = []
    raw_records = raw.get("records", [])
    computed_detail_count = sum(
        1
        for record in raw_records
        if str(record.get("extraction_method", "")).startswith("detail-page") or record.get("extraction_method") == "fixture"
    )
    computed_api_detail_count = sum(1 for record in raw_records if record.get("extraction_method") == "detail-api fallback")
    computed_evidence_count = sum(
        1
        for record in raw_records
        if record.get("extraction_method") != "search-card fallback" and (record.get("text") or record.get("comments"))
    )
    computed_comment_record_count = sum(1 for record in raw_records if record.get("comments"))
    computed_total_comments = sum(len(record.get("comments", []) or []) for record in raw_records)

    structured_notes = [structured_note(record, request) for record in raw_records]

    for record in raw.get("records", []):
        observation = make_observation(record, request)
        if observation["research_score"] >= 42:
            observations.append(observation)
        elif observation["research_score"] >= 24:
            report_url, report_url_type, source_url_valid = select_report_source_url(record)
            leads.append(
                {
                    "title": record.get("title", ""),
                    "url": report_url,
                    "raw_url": record.get("url", ""),
                    "report_source_url": report_url,
                    "report_source_url_type": report_url_type,
                    "source_url_valid": source_url_valid,
                    "query": record.get("query", ""),
                    "reason": "分数偏低但可能有待核实信息",
                    "score": observation["research_score"],
                    "theme": observation["theme"],
                    "noise_reasons": observation["noise_reasons"],
                }
            )
        else:
            report_url, report_url_type, source_url_valid = select_report_source_url(record)
            dropped_noise.append(
                {
                    "title": record.get("title", ""),
                    "url": report_url,
                    "raw_url": record.get("url", ""),
                    "report_source_url": report_url,
                    "report_source_url_type": report_url_type,
                    "source_url_valid": source_url_valid,
                    "score": observation["research_score"],
                    "noise_reasons": observation["noise_reasons"],
                }
            )

    observations.sort(key=lambda item: item["research_score"], reverse=True)
    generated_at = datetime.now(CN_TZ).isoformat(timespec="seconds")
    return {
        "meta": {
            "topic": request.get("meta", {}).get("topic", ""),
            "query": request.get("query", ""),
            "focus_topics": request.get("focus_topics", []),
            "generated_at": generated_at,
            "source": "xiaohongshu",
            "sort": raw.get("meta", {}).get("sort", "general"),
            "records_seen": len(raw_records),
            "observation_count": len(observations),
            "lead_count": len(leads),
            "noise_count": len(dropped_noise),
            "warning_count": len(raw.get("warnings", [])) + len(raw.get("errors", [])),
            "sample_fixture": raw.get("meta", {}).get("sample_fixture", False),
            "detail_record_count": raw.get("meta", {}).get("detail_record_count", computed_detail_count),
            "api_detail_record_count": raw.get("meta", {}).get("api_detail_record_count", computed_api_detail_count),
            "evidence_record_count": raw.get("meta", {}).get("evidence_record_count", computed_evidence_count),
            "detail_attempted_count": raw.get("meta", {}).get("detail_attempted_count", 0),
            "detail_success_count": raw.get("meta", {}).get("detail_success_count", 0),
            "detail_success_rate": raw.get("meta", {}).get("detail_success_rate", 0),
            "collection_incomplete": raw.get("meta", {}).get("collection_incomplete", False),
            "collection_status": raw.get("meta", {}).get("collection_status", "unknown"),
            "detail_open_audit_json": raw.get("meta", {}).get("detail_open_audit_json", ""),
            "detail_open_audit_md": raw.get("meta", {}).get("detail_open_audit_md", ""),
            "comment_record_count": raw.get("meta", {}).get("comment_record_count", computed_comment_record_count),
            "total_comment_count": raw.get("meta", {}).get("total_comment_count", computed_total_comments),
            "fallback_record_count": raw.get("meta", {}).get(
                "fallback_record_count",
                max(0, len(raw_records) - computed_evidence_count),
            ),
            "skipped_existing_count": raw.get("meta", {}).get("skipped_existing_count", 0),
            "collected_index": raw.get("meta", {}).get("collected_index", ""),
        },
        "methodology": {
            "scoring": [
                "正文信息量、评论区实用补充、产业/制度/价格/流动信号加分",
                "广告口吻、提问帖、只有搜索卡片或缺少详情页降权",
                "互动量只作为弱信号，高互动不会覆盖弱内容",
            ],
            "uncertainty": "单平台内容只作为趋势线索；结论需要和行业数据、政策、招聘、价格、交通或历史资料交叉验证。",
        },
        "theme_options": list(LOGIC_TEMPLATES.keys()),
        "themes": theme_summary(observations),
        "structured_notes": structured_notes,
        "ai_summary": build_ai_summary(observations, structured_notes),
        "observations": observations,
        "leads_to_verify": leads,
        "dropped_noise": dropped_noise[:30],
        "raw_warnings": raw.get("warnings", []),
        "raw_errors": raw.get("errors", []),
        "request": request,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize XHS notes into trend observations.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    request = load_json(args.request)
    raw = load_json(args.raw)
    normalized = normalize(request, raw)
    dump_json(args.out, normalized)
    print(f"normalized research written: {args.out}")
    print(
        f"observations={normalized['meta']['observation_count']} "
        f"leads={normalized['meta']['lead_count']} "
        f"noise={normalized['meta']['noise_count']}"
    )


if __name__ == "__main__":
    main()
