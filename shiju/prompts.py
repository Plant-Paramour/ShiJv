from __future__ import annotations

import json
from pathlib import Path

from .data import MeterTemplate
from .domain import CONTENT_MARKER, TITLE_MARKER


RHYME_NAMES = {
    "Cilin": "词林正韵",
    "Pinshui": "平水韵",
    "Xinyun": "中华新韵",
    "Tongyun": "中华通韵",
}


def _apply_thinking(messages: list[dict[str, str]], use_thinking: bool) -> list[dict[str, str]]:
    if not use_thinking:
        for message in messages:
            if message["role"] == "user":
                message["content"] = "/no_think " + message["content"]
    return messages


def _output_contract(
    title_prefix: str,
    planning_instruction: str | None = None,
    planning_label: str = "创作构思分析",
    planning_marker: str | None = None,
    skip_planning: bool = False,
) -> str:
    planning = planning_instruction or (
        "先输出创作构思，必须包含以下五项，每项一至三句，"
        "分析中不得提前写出诗句或词句草稿：\n"
        "1. 主线：从事理线、情景线、情感线、隐喻线中明确选择一条，"
        "并用一句话说清全篇如何推进。\n"
        "2. 写前四定：对象、主线、转折位置及原因、最终落点。\n"
        "3. 分段结构：按起承转收或词牌上下阕/多叠，说明每段承担的"
        "叙事、视线或情绪功能。\n"
        "4. 核心意象：指定一个中心意象或隐喻系统，说明前后如何照应，"
        "不得堆砌孤立意象。\n"
        "5. 具体信息：把抽象情感落实为动作、物件、声音、时间或空间细节。"
    )
    planning_step = f"1. 先输出{planning_label}。\n"
    planning_example = ""
    if planning_marker:
        planning_step = (
            f"1. 先输出{planning_label}，第一行必须以 {planning_marker} 开头。\n"
        )
        planning_example = f"{planning_marker}简短创作方向与安排\n"
    if skip_planning:
        return (
            "你是一位擅长古典诗词创作的作者。\n\n"
            "创作时必须遵守以下优先级：\n"
            "1. 用户给出的核心叙事必须全部落实在正文中，不得只在标题或说明中提及。\n"
            "2. 现代语义须转换为自然的古典表达，但不得省略人物、时间、事件、关切或情感。\n"
            "3. 正文须符合指定诗体、主题和古典诗词审美。\n"
            "4. 同时根据格律约束安排字词，不得为了格律省略核心叙事。\n\n"
            "写作时先在心中安排全篇结构、意象和情感推进，但不要输出构思、规划、分析或思维过程。\n\n"
            "必须直接按以下格式输出标题和正文：\n"
            f"{TITLE_MARKER}{title_prefix}·作品名\n"
            f"{CONTENT_MARKER}正文\n\n"
            f"完整格式示例：\n{TITLE_MARKER}{title_prefix}·春思\n"
            f"{CONTENT_MARKER}柳色含烟……\n\n"
            f"标题中的“{title_prefix}”不可省略，也不得只输出作品名。"
            f"{TITLE_MARKER} 和 {CONTENT_MARKER} 两个标记均不可省略、修改或替换；"
            f"正文第一个字之前必须原样输出 {CONTENT_MARKER}。\n"
            "不得输出创作构思、规划、分析、思维过程、格律说明、注释或其他额外文字。"
            "正文不得包含段落标记、序号、解释性文字或“平”“仄”“中”“/”等格律符号。"
            "标题和正文之后不得输出任何额外内容。"
        )
    return (
        "你是一位擅长古典诗词创作的作者。\n\n"
        "创作时必须遵守以下优先级：\n"
        "1. 用户给出的核心叙事必须全部落实在正文中，不得只在标题或说明中提及。\n"
        "2. 现代语义须转换为自然的古典表达，但不得省略人物、时间、事件、关切或情感。\n"
        "3. 正文须符合指定诗体、主题和古典诗词审美。\n"
        "4. 同时根据格律约束安排字词，不得为了格律省略核心叙事。\n\n"
        "写作条理要求：先确定一条贯穿全篇的事理线、情景线、情感线或隐喻线，再逐句落笔。\n"
        "写前明确对象、主线、转折及落点；写中每句必须推进主线、移动视线、加深情绪或提供具体信息。\n"
        "跳跃必须有因果、对比、重复、反义或物象同构等暗桥；抽象情感必须落到动作、物件、声音、时间或空间。\n"
        "围绕一个中心意象建立前后照应的系统，不堆砌孤立意象；结尾用动作、物象或反转收束，不总结、不口号化升华。\n\n"
        f"{planning}\n\n"
        "必须按以下格式输出：\n"
        f"{planning_step}"
        f"2. {planning_label}结束后，严格按照下面的完整模板输出标题和正文：\n"
        f"{TITLE_MARKER}{title_prefix}·作品名\n"
        f"{CONTENT_MARKER}正文\n\n"
        "完整格式示例：\n"
        f"{planning_example}"
        f"{TITLE_MARKER}{title_prefix}·春思\n"
        f"{CONTENT_MARKER}柳色含烟……\n\n"
        f"标题中的“{title_prefix}”不可省略，也不得只输出作品名。"
        f"{TITLE_MARKER} 和 {CONTENT_MARKER} 两个标记均不可省略、修改或替换；"
        f"正文第一个字之前必须原样输出 {CONTENT_MARKER}。\n"
        "除前述创作构思或规划外，不得输出格律说明、注释或其他额外文字；"
        "标题和正文之后不得输出任何额外内容。正文不得包含段落标记、"
        "序号、解释性文字或“平”“仄”“中”“/”等格律符号。"
    )


def _narrative_section(requirement: str) -> str:
    if not requirement:
        return "【核心叙事】\n请围绕主题完成完整、具体的情感表达。"
    return (
        "【核心叙事】\n"
        "以下要求必须全部体现在正文中，不得只表现笼统的主题或相思；"
        "可以改写为古典语汇，但不得省略或泛化：\n"
        f"{requirement}\n"
        "写作前请在心里逐项确认：每一项对应正文哪一句或哪几句？"
        "若有任何一项无法落实，必须先调整结构，不得跳过。"
    )


def _style_section(theme: str) -> str:
    theme_line = f"主题：{theme}\n" if theme.strip() else ""
    return "【情感与风格】\n" + theme_line + "表达须含蓄典雅、情景交融，避免直白口语和泛泛堆砌相思意象。"


def _final_section(title_prefix: str, planning_label: str = "创作分析") -> str:
    return (
        "【最终要求】\n"
        "正文篇幅有限时，优先保证核心叙事完整，其次考虑意象、用典和辞藻。\n"
        f"请严格按{planning_label}、标题、正文的顺序输出。规划结束后必须紧接：\n"
        f"{TITLE_MARKER}{title_prefix}·作品名\n"
        f"{CONTENT_MARKER}正文\n"
        f"不得省略“{title_prefix}”，也不得省略或改写 {TITLE_MARKER}、{CONTENT_MARKER}。"
    )


def _template_meter_section(template: MeterTemplate, rhyme_name: str) -> str:
    rules = [
        "【格律约束】",
        f"采用变体：{template.variant_name}",
        f"用韵依据：{rhyme_name}",
        f"韵式：{template.rhyme_type}",
        "以下格律只供理解，格律符号不得出现在正文中。"
        "其中“/”表示句内节奏边界且不输出，“、”表示必须输出顿号。",
    ]
    for stanza in template.stanzas:
        rules.append(f"第{stanza.index + 1}阕：")
        for line_index, line in enumerate(stanza.lines, start=1):
            rhyme_note = f"，押第{line.rhyme_group}组韵" if line.rhyme_group else ""
            rules.append(
                f"- 第{line_index}句（{line.layout.length}字）：{line.raw_pattern}{rhyme_note}"
            )
    if template.repetition_groups:
        rules.append("叠加要求：阕、句、字序号均从1开始，起止位置均包含。以下每组位置必须按对应类别执行：")
        for group in template.repetition_groups:
            positions = group.get("positions", [])
            rendered = []
            for position in positions:
                if not isinstance(position, dict):
                    continue
                rendered.append(
                    f"第{position.get('stanza')}阕第{position.get('start', ['?','?'])[0]}句第{position.get('start', ['?','?'])[1]}字"
                    f"至第{position.get('end', ['?','?'])[0]}句第{position.get('end', ['?','?'])[1]}字"
                )
            if rendered:
                kind = group.get("kind", "叠字")
                instructions = {
                    "叠韵": "这些范围包含韵脚，范围内每个字必须相同",
                    "叠句": "这些范围是完整句子，整句文字必须逐字相同",
                    "叠字": "这些范围是句内字，范围内每个字必须相同",
                }
                rules.append(
                    f"- {kind}组{group.get('id', '?')}：" + "；".join(rendered)
                    + f"。{instructions.get(kind, instructions['叠字'])}。"
                )
    return "\n".join(rules)


def _template_task_prompt(
    name: str,
    rhyme_name: str,
    theme: str,
    requirement: str,
    variant_name: str | None = None,
    meter_text: str = "",
    has_repetition: bool = False,
) -> str:
    variant_line = f"\n变体：{variant_name}" if variant_name else ""
    meter_section = f"{meter_text}\n\n" if meter_text else ""
    repetition_note = (
        "\n本词牌含叠字/叠句/叠韵要求，规划时必须说明这些位置"
        "如何与主线、意象系统配合，不得为叠而叠。\n"
        if has_repetition
        else ""
    )
    return (
        "【创作形式】\n"
        f"词牌：{name}{variant_line}\n"
        f"用韵：{rhyme_name}\n"
        "词牌的字数、句读、平仄和押韵须严格遵守。\n"
        f"{repetition_note}\n"
        f"{_narrative_section(requirement)}\n\n"
        f"{_style_section(theme)}\n\n"
        f"{meter_section}"
        f"{_final_section(name)}"
    )


def _relational_task_prompt(
    form_name: str,
    rhyme_name: str,
    theme: str,
    line_length: int,
    num_lines: int,
    requirement: str,
    allow_aojiu: bool,
) -> str:
    length_name = "五言" if line_length == 5 else "七言"
    form_type = "绝句" if num_lines == 4 else "律诗"
    aojiu_rule = (
        "非韵句允许四拗三救、六拗五救、特拗交换及对应的对句相救"
        if allow_aojiu
        else "不安排拗救"
    )
    rules = (
        "【格律约束】\n"
        "以下格律须严格遵守：\n"
        f"- 每句 {line_length} 字，共 {num_lines} 句\n"
        "- 严格遵守二四六分明：第2字决定基调，第4字与第2字相反，第6字与第2字相同\n"
        "- 奇数句以仄声收尾，偶数句以平声收尾\n"
        "- 所有偶数句必须押同一韵部，一韵到底\n"
        f"- 韵句禁止孤平和三平尾，非韵句允许三仄尾；{aojiu_rule}\n"
        "- 不以“的”“些”“么”“了”等现代白话虚词入诗"
    )
    return (
        "【创作形式】\n"
        f"诗体：{form_name}（{length_name}{form_type}）\n"
        f"用韵：{rhyme_name}\n"
        f"篇幅：每句{line_length}字，共{num_lines}句\n"
        "字数、句数、句读、平仄和押韵须严格遵守。\n\n"
        f"{_narrative_section(requirement)}\n\n"
        f"{_style_section(theme)}\n\n"
        f"{rules}\n\n"
        f"{_final_section(form_name)}"
    )


def _pailv_task_prompt(
    form_name: str,
    rhyme_name: str,
    theme: str,
    line_length: int,
    num_lines: int,
    requirement: str,
    allow_aojiu: bool,
) -> str:
    length_name = "五言" if line_length == 5 else "七言"
    aojiu_rule = (
        "非韵句只允许四拗三救、六拗五救、特拗交换及对应的对句相救；"
        "韵句孤平只能由第三字或第五字自救，二四六不得因拗救而放宽"
        if allow_aojiu
        else "不安排拗救"
    )
    rules = (
        "【排律格律约束】\n"
        "以下格律须严格遵守：\n"
        f"- 每句 {line_length} 字，共 {num_lines} 句；句数必须保持偶数\n"
        "- 严格遵守替、对、粘：二四六等关键位置相替、相对、相粘，整篇按联次延续\n"
        "- 偶数句以平声押韵，首句可押可不押；首句若押，必须与后文使用同一韵部\n"
        "- 除首句外，奇数句以仄声收尾；偶数句全部押同一平声韵，一韵到底，不换韵、不通押邻韵\n"
        f"- 韵句禁止孤平和三平尾，非韵句允许三仄尾；{aojiu_rule}\n"
        "- 对仗：首联、尾联可以不对，中间各联必须逐联对仗；允许使用扇面对等变格\n"
        "- 不以“的”“些”“么”“了”等现代白话虚词入诗"
    )
    return (
        "【创作形式】\n"
        f"诗体：{form_name}（{length_name}排律）\n"
        f"用韵：{rhyme_name}\n"
        f"篇幅：每句{line_length}字，共{num_lines}句\n"
        "字数、句数、句读、平仄和押韵须严格遵守。\n\n"
        f"{_narrative_section(requirement)}\n\n"
        f"{_style_section(theme)}\n\n"
        f"{rules}\n\n"
        f"{_final_section(form_name)}"
    )


def build_template_prompt(
    task_type: str,
    template: MeterTemplate,
    theme: str,
    requirement: str = "",
    use_thinking: bool = True,
    rhyme_dict_name: str = "Cilin",
    cipai_data_path: str = "PoeTone-main/data/cipai_data.json",
    examples_dir: str | Path = "Songci_Meter/examples",
    skip_planning: bool = False,
) -> list[dict[str, str]]:
    name = template.name
    rhyme_name = RHYME_NAMES.get(rhyme_dict_name, rhyme_dict_name)

    if task_type == "zero-shot":
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一位精通宋代词学的词人，擅长以典雅凝练的古典语汇营造深远意境。\n\n"
                    + _output_contract(name, skip_planning=skip_planning)
                ),
            },
            {
                "role": "user",
                "content": _template_task_prompt(name, rhyme_name, theme, requirement),
            },
        ]
        return _apply_thinking(messages, use_thinking)

    if task_type == "one-shot":
        example_path = Path(examples_dir) / f"{name}-例文.json"
        if not example_path.is_file():
            raise FileNotFoundError(f"词牌 {name} 的 one-shot 范例不存在: {example_path}")
        with example_path.open("r", encoding="utf-8") as stream:
            example_data = json.load(stream)
        variants = example_data.get("variants", [])
        def usable_stanzas(variant):
            return sorted(
                (
                    key for key, stanza in variant.items()
                    if key.startswith("stanza") and key[6:].isdigit()
                    and isinstance(stanza.get("lines"), list) and stanza["lines"]
                ),
                key=lambda key: int(key[6:]),
            )

        usable_variants = [item for item in variants if usable_stanzas(item)]
        selected = next(
            (item for item in usable_variants if item.get("name") == template.variant_name),
            usable_variants[0] if usable_variants else None,
        )
        if not selected:
            raise ValueError(f"词牌 {name} 的范例文件没有含词句的可用变体: {example_path}")
        stanzas = usable_stanzas(selected)
        rendered_stanzas = []
        for index, stanza_name in enumerate(stanzas, start=1):
            lines = selected[stanza_name].get("lines", [])
            rendered_stanzas.append(f"第{index}阕：\n" + "\n".join(lines))
        if not rendered_stanzas:
            raise ValueError(f"词牌 {name} 的范例变体没有有效词句: {example_path}")
        example = (
            f"词牌：{name}\n变体：{selected.get('name', template.variant_name)}\n"
            f"作者：{selected.get('author', '范例数据未注明')}\n\n"
            + "\n\n".join(rendered_stanzas)
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一位精通宋代词学的词人，擅长研读词作的意象、章法与语言风格，"
                    "并融会其神韵创作完全原创的新词。范例只作学习参考，严禁挪用原句。\n\n"
                    + _output_contract(name, skip_planning=skip_planning)
                ),
            },
            {
                "role": "user",
                "content": (
                    f"【所选词牌范例】\n{example}\n\n"
                    "请学习范例的意象经营、上下阕章法与语言风格，效仿神韵而非字句，"
                    "不得复用范例原句。\n\n"
                    f"{_template_task_prompt(name, rhyme_name, theme, requirement, template.variant_name, _template_meter_section(template, rhyme_name))}"
                ),
            },
        ]
        return _apply_thinking(messages, use_thinking)

    if task_type == "completion":
        path = Path(cipai_data_path)
        if not path.exists():
            raise FileNotFoundError(
                f"{cipai_data_path} 不存在，{task_type} 依赖仓库外部示例数据"
            )
        with path.open("r", encoding="utf-8") as stream:
            external_data = json.load(stream)
        if task_type == "one-shot":
            example = external_data["one_shot_examples"].get(name, "")
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一位精通宋代词学的词人，擅长揣摩前人词作的风格神韵，"
                        "并能融会贯通、推陈出新。请研读范例后创作具有独立艺术价值的新词。\n\n"
                        + _output_contract(name, skip_planning=skip_planning)
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"这是《{name}》的范例：\n\n{example}\n\n"
                        "请揣摩其意象、章法和语言风格，效仿神韵而非字句。\n\n"
                        f"{_template_task_prompt(name, rhyme_name, theme, requirement)}"
                    ),
                },
            ]
        else:
            first_half = external_data["completion_data"].get(name, {}).get("first_half", "")
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一位精通宋代词学的词人，尤其擅长承上启下、续写词章。"
                        "下阕须承接上阕意脉，又能翻出新意并升华主题，且必须完全原创。\n\n"
                        + _output_contract(name, skip_planning=skip_planning)
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"这是《{name}》的上阕：\n\n{first_half}\n\n"
                        "请承接上阕意脉、深化情感或转出新境，并完全原创。\n\n"
                        f"{_template_task_prompt(name, rhyme_name, theme, requirement)}"
                    ),
                },
            ]
        return _apply_thinking(messages, use_thinking)

    if task_type != "instruction":
        raise ValueError(f"宋词不支持任务类型: {task_type}")

    messages = [
        {
            "role": "system",
            "content": (
                "你是一位精通宋代词学的词人，深谙词牌格律、意象经营与章法布局，追求字字珠玑、"
                "句句有意。\n\n" + _output_contract(name, skip_planning=skip_planning)
            ),
        },
        {
            "role": "user",
            "content": _template_task_prompt(
                name,
                rhyme_name,
                theme,
                requirement,
                template.variant_name,
                _template_meter_section(template, rhyme_name),
            ),
        },
    ]
    return _apply_thinking(messages, use_thinking)


def build_relational_prompt(
    task_type: str,
    form_name: str,
    theme: str,
    line_length: int,
    num_lines: int,
    requirement: str = "",
    use_thinking: bool = True,
    rhyme_dict_name: str = "Pinshui",
    allow_aojiu: bool = False,
    skip_planning: bool = False,
) -> list[dict[str, str]]:
    if task_type != "instruction":
        raise ValueError(f"唐诗当前只支持 instruction，收到: {task_type}")
    rhyme_name = RHYME_NAMES.get(rhyme_dict_name, rhyme_dict_name)
    messages = [
        {
            "role": "system",
            "content": "你是一位擅长唐诗创作的诗人。\n\n"
            + _output_contract(form_name, skip_planning=skip_planning),
        },
        {
            "role": "user",
            "content": _relational_task_prompt(
                form_name,
                rhyme_name,
                theme,
                line_length,
                num_lines,
                requirement,
                allow_aojiu,
            ),
        },
    ]
    return _apply_thinking(messages, use_thinking)


def build_pailv_prompt(
    task_type: str,
    form_name: str,
    theme: str,
    line_length: int,
    num_lines: int,
    requirement: str = "",
    use_thinking: bool = True,
    rhyme_dict_name: str = "Pinshui",
    allow_aojiu: bool = True,
    skip_planning: bool = False,
) -> list[dict[str, str]]:
    if task_type != "instruction":
        raise ValueError(f"排律当前只支持 instruction，收到: {task_type}")
    rhyme_name = RHYME_NAMES.get(rhyme_dict_name, rhyme_dict_name)
    messages = [
        {
            "role": "system",
            "content": "你是一位擅长排律创作的诗人，熟悉联次章法、对仗和声韵格律。\n\n"
            + _output_contract(form_name, skip_planning=skip_planning),
        },
        {
            "role": "user",
            "content": _pailv_task_prompt(
                form_name,
                rhyme_name,
                theme,
                line_length,
                num_lines,
                requirement,
                allow_aojiu,
            ),
        },
    ]
    return _apply_thinking(messages, use_thinking)


def build_hanpai_prompt(
    task_type: str,
    form_name: str,
    theme: str,
    line_lengths: tuple[int, int, int],
    requirement: str = "",
    use_thinking: bool = True,
    rhyme_dict_name: str = "Xinyun",
    season_word: str | None = None,
    season_words: tuple[str, ...] = (),
    season: str | None = None,
    forbid_isolated_level: bool = True,
    allow_aojiu: bool = True,
    forbid_three_same_ending: bool = True,
    rhyme_scheme: str | None = None,
    skip_planning: bool = False,
) -> list[dict[str, str]]:
    if task_type != "one-shot":
        raise ValueError(f"汉俳不支持任务类型: {task_type}")

    examples = (
        "闲寂古池旁\n青蛙跳进水中央\n扑通一声响\n\n"
        "绿荫今雨来\n山花枝接海花开\n和风起汉俳\n\n"
        "和风化细雨\n樱花吐艳迎朋友\n冬去春来早"
        if line_lengths == (5, 7, 5) else "念三遍\n猴王满地转\n不服管"
    )
    pattern = "-".join(str(length) for length in line_lengths)
    rhyme_name = RHYME_NAMES.get(rhyme_dict_name, rhyme_dict_name)
    season_inputs = (season_word is not None, bool(season_words), season is not None)
    if sum(season_inputs) > 1:
        raise ValueError("季语、候选季语和季节只能配置其中一种")
    if season_word is not None:
        season_rule = (
            f"必须在正文中原样、自然地使用指定季语“{season_word.strip()}”，"
            "不得只在标题或创作分析中提及。"
        )
    elif season_words:
        candidates = "、".join(f"“{word.strip()}”" for word in season_words)
        season_rule = (
            f"必须且只能从候选季语 {candidates} 中选择一个，并在正文中原样、"
            "自然地使用；不得只在标题或创作分析中提及。"
        )
    elif season is not None:
        season_rule = (
            f"指定季节为“{season.strip()}”。必须在正文中使用一个无需解释即可明显指向"
            "该季节的具体季语或季节意象，不得只写季节名称代替具体意象。"
        )
    else:
        season_rule = (
            "未指定季语或季节，但正文仍必须自行选择并使用一个明显、具体的季语或"
            "季节意象。"
        )
    season_rule += (
        "读者必须仅凭正文即可判断相应季节；未加时令限定的云、月、风、雨、柳等"
        "一般景物不视为明显季语。"
    )

    prosody_rules = []
    if forbid_isolated_level:
        if allow_aojiu:
            prosody_rules.append("禁止未获补救的孤平，允许使用邻位平声完成拗救")
        else:
            prosody_rules.append("禁止孤平")
    elif allow_aojiu:
        prosody_rules.append("允许自然使用拗救，但不强制安排")
    if forbid_three_same_ending:
        prosody_rules.append("每行句尾不得出现三连平或三连仄")
    prosody_text = "；".join(prosody_rules) if prosody_rules else "不额外限定平仄格律"

    normalized_scheme = rhyme_scheme.upper().strip() if rhyme_scheme else None
    rhyme_descriptions = {
        "AAA": "三行句尾均押同一韵部",
        "ABA": "第一、三行句尾押同一韵部，第二行不押",
        "BAA": "第二、三行句尾押同一韵部，第一行不押",
    }
    if normalized_scheme and normalized_scheme not in rhyme_descriptions:
        raise ValueError(
            f"汉俳押韵格式仅支持 AAA、ABA、BAA 或不押韵，收到: {rhyme_scheme}"
        )
    if normalized_scheme:
        rhyme_text = (
            f"采用 {normalized_scheme} 式：{rhyme_descriptions[normalized_scheme]}；"
            f"依据{rhyme_name}，首个押韵句确定声调和韵部，后续不得换韵。"
        )
    else:
        rhyme_text = "不设置押韵要求。"

    rules = (
        "【汉俳规则】\n"
        f"- 格式：{pattern}，正文恰好三行，依次为"
        f"{line_lengths[0]}字、{line_lengths[1]}字、{line_lengths[2]}字\n"
        "- 句读：五字句严格按 2/3 划分，七字句严格按 2/2/3 划分；"
        "每个节奏段必须独立成意，不得把一个词拆到边界两侧；这些是内部字数约束，"
        "正文中不得输出顿号、逗号、句号等句内标点\n"
        "- 行间只换行，换行作为句子结束边界\n"
        f"- 季语：{season_rule}\n"
        f"- 格律：{prosody_text}\n"
        f"- 押韵：{rhyme_text}\n"
        "- 语言凝练，三行须共同构成一次完整的观察、转折或余韵"
    )
    planning_instruction = (
        "汉俳正式写作前，先输出一小段简短的创作规划，控制在 2 至 4 句：说明核心叙事"
        "如何分配到三行、准备使用的明显季语或季节意象、情景推进方式，以及押韵和格律的"
        "取舍。这段规划必须作为可见答案输出并以 [plan] 开头，不能放进 <think> 标签。"
        "规划只写创作方向，不要展开逐步思维链，不要提前写出完整诗句、分句或格律符号。"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是一位擅长汉俳创作的诗人，能够在极短篇幅中凝聚具体景象与情感。\n\n"
                + _output_contract(
                    form_name,
                    planning_instruction,
                    "创作规划",
                    "[plan]",
                    skip_planning=skip_planning,
                )
            ),
        },
        {
            "role": "user",
            "content": (
                "【创作形式】\n"
                f"诗体：{form_name}\n"
                f"篇幅：{pattern}\n\n"
                f"{_narrative_section(requirement)}\n\n"
                f"{_style_section(theme)}\n\n"
                f"{rules}\n\n"
                "【写作范例】\n以下范例用于学习凝练表达和三行推进；实际创作必须遵守本次季语、格律和押韵要求，不得照抄。\n"
                f"{examples}\n\n"
                f"{_final_section(form_name, '创作规划')}"
            ),
        },
    ]
    return _apply_thinking(messages, use_thinking)


