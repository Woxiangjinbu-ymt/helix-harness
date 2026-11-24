"""装配一个已经冻结的行为策略，不扫描工作区或用户目录。"""

from pathlib import Path
import re
from dataclasses import dataclass

import yaml

from mewcode.skills.parser import parse_frontmatter, parse_skill_file
from .fixtures import regular_files, tree_digest


@dataclass(frozen=True)
class StrategyInputs:
    instructions: str
    skills: dict


def assemble_strategy(folder: Path, expected: str) -> StrategyInputs:
    if tree_digest(folder) != expected:
        raise ValueError("策略快照摘要不匹配")
    skills = {}
    for path in regular_files(folder):
        rel = path.relative_to(folder)
        if str(rel) in {"prompt.md", "workflow.yaml"}:
            continue
        if len(rel.parts) != 3 or rel.parts[0] != "skills" or rel.name != "SKILL.md":
            raise ValueError("策略包含未授权文件")
        meta, _ = parse_frontmatter(path.read_text())
        skill = parse_skill_file(path)
        if (set(meta) - {"name", "description", "mode"} or skill.mode != "inline"
                or skill.name != rel.parent.name or not skill.prompt_body.strip()):
            raise ValueError("策略只接受同名的 inline Skill")
        skills[skill.name] = {"name": skill.name, "description": skill.description,
                              "prompt_body": skill.prompt_body.strip()}

    prompt = (folder / "prompt.md").read_text().strip()
    workflow = yaml.safe_load((folder / "workflow.yaml").read_text())
    if not prompt or not isinstance(workflow, dict) or set(workflow) != {"stages"}:
        raise ValueError("行为 Prompt 或 Workflow 无效")
    stages = workflow["stages"]
    if not isinstance(stages, list) or not stages:
        raise ValueError("Workflow 至少需要一个阶段")
    sections = [prompt, "## Workflow"]
    for index, stage in enumerate(stages, 1):
        if isinstance(stage, str):
            stage = {"name": stage}
        if not isinstance(stage, dict) or set(stage) - {"name", "skills"}:
            raise ValueError("Workflow 阶段只接受 name 和 skills")
        name = stage.get("name")
        names = stage.get("skills", [])
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise ValueError("Workflow 阶段名称无效")
        if not isinstance(names, list) or not all(isinstance(n, str) and n in skills for n in names):
            raise ValueError("Workflow 引用了未知 Skill")
        sections.append(f"{index}. {name}")
        for skill in names:
            sections.append(f"### {skill}\n{skills[skill]['prompt_body']}")
    if tree_digest(folder) != expected:
        raise ValueError("策略在装配期间发生变化")
    return StrategyInputs("\n\n".join(sections), skills)


class FrozenSkillLoader:
    def __init__(self, catalog):
        from mewcode.skills.parser import SkillDef
        self.skills = {name: SkillDef(**data) for name, data in catalog.items()}

    def get(self, name):
        return self.skills.get(name)

    def get_catalog(self):
        return [(s.name, s.description) for s in self.skills.values()]
