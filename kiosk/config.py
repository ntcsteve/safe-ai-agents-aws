"""config.env: where this runs. Three values, validated before any command, with no AWS calls.

Everything else is worked out from these: the model's regions come from its inference profile, so a
wrong model fails at terraform plan, before anything is created.
"""

import dataclasses
import re

KEYS = ("REGION", "MODEL", "SCENARIO")
REGION_SHAPE = re.compile(r"[a-z]{2}(-[a-z]+)+-\d+")
MODEL_SHAPE = re.compile(r"[a-z0-9][a-z0-9.:-]*")


class ConfigInvalid(ValueError):
    """config.env cannot be used. Nothing was run."""


@dataclasses.dataclass(frozen=True)
class Config:
    region: str
    model: str
    scenario: str

    def terraform_vars(self):
        return [f"-var=region={self.region}", f"-var=model={self.model}", f"-var=scenario={self.scenario}"]


def parse_config(text, scenario_names):
    """The Config in config.env's text, or ConfigInvalid naming the first bad line or value.
    scenario_names: the scenarios that exist, so a typo lists the real ones."""
    values = _read_lines(text)
    missing = [key for key in KEYS if key not in values]
    if missing:
        raise ConfigInvalid(f"{', '.join(missing)} missing")
    region, model, scenario = values["REGION"], values["MODEL"], values["SCENARIO"]
    if not REGION_SHAPE.fullmatch(region):
        raise ConfigInvalid(f"REGION is '{region}', expected a region code like ap-southeast-2")
    if not MODEL_SHAPE.fullmatch(model):
        raise ConfigInvalid(f"MODEL is '{model}', expected a Bedrock inference profile ID")
    if scenario not in scenario_names:
        raise ConfigInvalid(f"SCENARIO is '{scenario}', expected one of {', '.join(sorted(scenario_names))}")
    return Config(region=region, model=model, scenario=scenario)


def _read_lines(text):
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ConfigInvalid(f"'{line}' is not KEY=VALUE")
        if key not in KEYS:
            raise ConfigInvalid(f"unknown key '{key}', expected {', '.join(KEYS)}")
        if key in values:
            raise ConfigInvalid(f"{key} appears twice")
        values[key] = value
    return values
