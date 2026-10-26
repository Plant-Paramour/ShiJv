"""Resolve model-defined stopping tokens without assuming a model family."""
def eos_token_ids(tokenizer, model=None):
    tokens = set()
    sources = [tokenizer]
    if model is not None:
        config = getattr(model, "config", None)
        sources += [config, getattr(config, "text_config", None), getattr(model, "generation_config", None)]
    for source in sources:
        value = getattr(source, "eos_token_id", None)
        values = value if isinstance(value, (list, tuple, set)) else [value]
        tokens.update(v for v in values if isinstance(v, int) and not isinstance(v, bool) and v >= 0)
    if not tokens:
        raise ValueError("模型和分词器均未声明有效的 EOS 结束标记")
    return sorted(tokens)


def pad_token_id(tokenizer, model=None):
    for source in (tokenizer, getattr(model, "generation_config", None), getattr(model, "config", None)):
        value = getattr(source, "pad_token_id", None)
        if isinstance(value, int) and value >= 0:
            return value
    return eos_token_ids(tokenizer, model)[0]
