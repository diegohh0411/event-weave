from enum import StrEnum


class Stage(StrEnum):
    provide = 'provide'
    extract = 'extract'
    tag = 'tag'
    enrich = 'enrich'
    embed = 'embed'
    cluster = 'cluster'


PIPELINE: tuple[Stage, ...] = tuple(Stage)
OPTIONAL_STAGES = {Stage.tag}
ORDERED_STAGES = {Stage.cluster}


class UnitKind(StrEnum):
    run = 'run'
    document = 'document'
    mention = 'mention'


class Capability(StrEnum):
    llm = 'llm'
    system_one = 'system_one'
    embeddings = 'embeddings'
    extraction = 'extraction'


class Track(StrEnum):
    paper = 'paper'
    realistic = 'realistic'


class JobStatus(StrEnum):
    pending = 'pending'
    claimed = 'claimed'
    done = 'done'
    failed = 'failed'


class RunState(StrEnum):
    created = 'created'
    running = 'running'
    cancelled = 'cancelled'
    done = 'done'
