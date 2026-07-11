"""Skill registry and natural language intent mapping."""
import re
from config import SCRIPTS_DIR

# ─── Skill Registry ─────────────────────────────────────────────
SKILLS = {
    "bring_screwdriver_workspace": {
        "label": "Bring screwdriver to workspace",
        "description": "Pick up the screwdriver and place it on the black workspace",
        "script": "run_screwdriver_policy.sh",
        "type": "policy",
        "icon": "🤖",
    },
    "test_cameras": {
        "label": "Test cameras",
        "description": "Check all three cameras are connected and reading frames",
        "script": "test_cameras.sh",
        "type": "diagnostic",
        "icon": "📷",
    },
    "check_dataset": {
        "label": "Check dataset",
        "description": "Verify dataset integrity: episodes, videos, counts",
        "script": "check_dataset.sh",
        "type": "diagnostic",
        "icon": "💾",
    },
}

# ─── Intent Mapping ─────────────────────────────────────────────
# Rule-based keyword → skill mapping
INTENT_RULES = [
    {
        "keywords": ["fix", "repair", "screwdriver", "tool", "bring", "grab", "get", "fetch", "pick"],
        "skill": "bring_screwdriver_workspace",
    },
    {
        "keywords": ["workspace", "work space", "prep", "prepare", "setup", "set up", "ready"],
        "skill": "bring_screwdriver_workspace",
    },
]


def interpret_text(text: str) -> dict:
    """Map natural language text to a skill using rule-based keyword matching."""
    text_lower = text.lower().strip()

    if not text_lower:
        return {
            "skill": None,
            "label": None,
            "message": "Please enter a command or request.",
            "confidence": None,
        }

    for rule in INTENT_RULES:
        for keyword in rule["keywords"]:
            if keyword in text_lower:
                skill_id = rule["skill"]
                skill = SKILLS[skill_id]
                return {
                    "skill": skill_id,
                    "label": skill["label"],
                    "message": f'Interpreted "{text}" → {skill["label"]}',
                    "confidence": "high",
                    "matched_keyword": keyword,
                }

    return {
        "skill": None,
        "label": None,
        "message": f'Could not interpret "{text}". Try mentioning: screwdriver, fix, workspace, prep.',
        "confidence": None,
    }


def get_skill(skill_id: str) -> dict | None:
    """Get skill info by ID. Returns None if not in allowlist."""
    return SKILLS.get(skill_id)


def get_script_path(skill_id: str) -> str | None:
    """Get the full path to a skill's script."""
    skill = SKILLS.get(skill_id)
    if not skill:
        return None
    return str(SCRIPTS_DIR / skill["script"])


def get_command(skill_id: str) -> list[str] | None:
    """Build the shell command list for a skill."""
    script_path = get_script_path(skill_id)
    if not script_path:
        return None
    return ["bash", script_path]


def get_command_string(skill_id: str) -> str | None:
    """Get the command as a string for display / dry run."""
    cmd = get_command(skill_id)
    if not cmd:
        return None
    return " ".join(cmd)


def get_curl_command(skill_id: str, host: str = "localhost:8000") -> str:
    """Generate curl command for calling a skill via the API."""
    return (
        f'curl -X POST http://{host}/api/skill '
        f'-H "Content-Type: application/json" '
        f'-d \'{{"skill": "{skill_id}", "confirm": true}}\''
    )


def list_skills() -> list[dict]:
    """List all available skills with metadata."""
    result = []
    for skill_id, info in SKILLS.items():
        result.append({
            "id": skill_id,
            "label": info["label"],
            "description": info["description"],
            "type": info["type"],
            "icon": info["icon"],
            "curl": get_curl_command(skill_id),
        })
    return result
