from __future__ import annotations

from ..contracts import RewritePoemRequest
from .parser import ParsedPoem


def build_rewrite_messages(
    request: RewritePoemRequest,
    poem: ParsedPoem,
) -> list[dict[str, str]]:
    first, last = request.target_line_numbers[0], request.target_line_numbers[-1]
    before = "\n".join(line.source_text for line in poem.lines[:first - 1]) or "（无）"
    after = "\n".join(line.source_text for line in poem.lines[last:]) or "（无）"
    if request.task_options.get("omit_original_target_from_prompt") is True:
        return _build_prompt_without_original_target(
            len(request.target_line_numbers), before, after, request.requirement.strip(),
        )
    target = "\n".join(line.source_text for line in poem.lines[first - 1:last])
    requirement = request.requirement.strip() or (
        "在忠于原作题旨和上下文的前提下，改善目标片段的表达，使语言自然、"
        "语意清楚、意象协调，并符合诗体特点。对用户未要求保留的表达，避免沿用原句的关键词组合、意象关系和句法框架；"
        "不为求异而生硬换词，允许必要的语义承接。"
    )
    system = (
        "你是一位熟悉中国古典诗词、格律和文本编辑的专业编辑，负责按用户要求修改指定诗句。\n\n"
        "指令优先级：用户在本次任务中提出的具体目标、保留项、禁用项和风格要求，决定本次改写的方向与幅度；"
        "本提示词中的通用编辑原则不得与之冲突。用户没有提出的改动，不要自行设为硬性要求。"
        "若用户明确要求保留某个词语、意象、句式或叙事信息，必须遵守；未明确要求保留的部分，按下述负面样本规则处理。\n\n"
        "编辑流程：\n"
        "1. 结合 BEFORE、REWRITE_TARGET、AFTER 理解全诗语境，区分目标句必须表达的意思、用户明确要求保留的内容，"
        "以及用户要求调整的部分。REWRITE_TARGET 是负面表达样本：只用于理解原意、叙事功能和修改缘由，不是可供续写或仿写的范本。\n"
        "2. 先准确完成用户要求，再处理表达。按要求决定保留或调整词语、意象、句式、语气和叙事信息；"
        "若用户只要求局部修改，不要擅自扩大改动；若用户要求重构，则围绕其目标重组表达。\n"
        "3. 对未被用户明确要求保留的部分，避免沿用原句的关键词组合、核心意象搭配、修辞关系和句法组织；"
        "应重新选择适合本次修改目标的表达路径，而不是在原句框架内逐词替换。用户明确要求保留的内容不受此限制。\n"
        "4. 不要把负面样本规则误解为必须彻底更换所有字词或意象。允许必要的承接性用词和符合诗体的常见表达；"
        "避免无依据增删信息、空泛堆砌和为求新奇而生硬造句。\n"
        "5. 保证修改后的片段语意清楚、语言自然、意象协调，并与未修改上下文衔接。遵循程序施加的格律和结构约束；"
        "在约束冲突时，不要用无意义的字词填充。\n"
        "6. 只输出指定目标片段和规定标记，不输出上下文、完整诗稿、分析、解释或思维过程。"
    )
    user = (
        "/no_think\n\n"
        "# 任务\n"
        f"根据上下文与本次修改目标，处理指定的 {len(request.target_line_numbers)} 个目标句。\n\n"
        "请以本次修改目标为准确定改动范围和方式。\n\n"
        "# 原作上下文\n\n"
        "<BEFORE>\n" + before + "\n</BEFORE>\n\n"
        "<REWRITE_TARGET>\n" + target + "\n</REWRITE_TARGET>\n\n"
        "<AFTER>\n" + after + "\n</AFTER>\n\n"
        "# 本次修改目标\n\n" + requirement + "\n\n"
        "# 编辑要求\n\n"
        "1. 忠实执行本次修改目标。目标中明确要求保留、替换、弱化、强化或避免的内容，按其要求处理；"
        "不要把通用写作偏好凌驾于这些具体要求之上。\n"
        "2. 将目标句作为一个片段整体把握，确保各句之间的语意关系、视角、情绪和衔接成立；"
        "除非用户要求，不要为了连贯而改变未指定的事实或立意。\n"
        "3. 对用户未指定保留的表达，不沿用原句的关键词组合、意象关系或句法框架；要围绕用户目标另行组织语言。"
        "不要求字字避同，必要的语义承接和诗体常用词可以保留。\n"
        "4. 每句都应语意完整、表达自然。避免机械换词、同义反复、堆砌意象、语法倒装和无意义的虚词填充。\n"
        "5. 语言应符合原作的诗体和语境，保持凝练、准确、协调；自然承接 BEFORE，并为 AFTER 留出合理衔接。\n\n"
        "# 输出协议\n\n"
        "输出第一行为：\n"
        "[rewrite]\n\n"
        "随后仅输出按本次修改目标处理后的目标片段。\n\n"
        "不得输出其他内容。"
    )
    return [
        {
            "role": "system",
            "content": system,
        },
        {
            "role": "user",
            "content": user,
        },
    ]


def _build_prompt_without_original_target(
    target_count: int,
    before: str,
    after: str,
    requirement: str,
) -> list[dict[str, str]]:
    if not requirement:
        requirement = (
            "结合前后文把握语意、视角、情绪和衔接，依据本次要求创作目标句。"
            "若没有给出更具体的方向，优先做到语意连贯、表达自然、准确、凝练。"
        )
    system = (
        "你是一位熟悉中国古典诗词、格律和文本编辑的专业编辑。请根据用户提出的具体要求，为指定位置创作诗句。\n\n"
        "用户的本次要求决定创作内容、改动方向、表达风格和信息取舍。不要自行增加用户未要求的限制，也不要擅自改变用户明确指定的内容。\n\n"
        "结合前后文把握语意、视角、情绪和衔接。只依据提示中提供的信息，不补造未提供的人物、事件或背景。"
        "遵循程序施加的格律与结构约束；表达应自然、准确、凝练，避免堆砌意象和生硬造句。\n\n"
        "只输出指定位置的新诗句和规定标记，不输出其他内容。"
    )
    user = (
        "/no_think\n\n"
        "# 任务\n"
        f"根据本次要求，为指定位置创作 {target_count} 句诗。\n\n"
        "# 前文\n"
        f"{before}\n\n"
        "# 后文\n"
        f"{after}\n\n"
        "# 本次创作要求\n"
        f"{requirement}\n\n"
        "# 格律与结构\n"
        "按系统施加的格律和结构约束创作。句数与顺序须符合要求。\n\n"
        "# 输出格式\n"
        "第一行输出：\n"
        "[rewrite]\n\n"
        "随后只输出新创作的目标诗句，不输出前后文、分析或说明。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
