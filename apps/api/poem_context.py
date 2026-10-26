"""Fields safe to transfer with a public poem, including across ownership."""

PUBLIC_CONTEXT_FIELDS = frozenset({
    "meter_type", "form_name", "variant_name", "rhyme_dict_name", "rhyme_mode",
    "rhyme_parts", "num_lines", "strict_polyphonic",
})


def public_poem_context(context):
    if not isinstance(context, dict):
        return {}
    result = {key: value for key, value in context.items() if key in PUBLIC_CONTEXT_FIELDS}
    options = context.get("task_options")
    if isinstance(options, dict):
        safe_options = {key: options[key] for key in ("line_pattern", "variant_name") if key in options}
        if safe_options:
            result["task_options"] = safe_options
    return result
