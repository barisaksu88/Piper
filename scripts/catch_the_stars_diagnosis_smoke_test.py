from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness

BROKEN_SCRIPT = """import pygame
import random
import sys

pygame.init()

SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
BASKET_WIDTH = 100
BASKET_HEIGHT = 50
STAR_SIZE = 30
STAR_SPEED = 3
SCORE_PER_STAR = 10
SCORE_PENALTY = 20

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
YELLOW = (255, 255, 0)
RED = (255, 0, 0)
BLUE = (0, 0, 255)

screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
pygame.display.set_caption("Catch the Falling Stars")

font = pygame.font.SysFont("Arial", 24)
big_font = pygame.font.SysFont("Arial", 48)


class Basket(pygame.sprite.Sprite):
    def __init__(self):
        super().__init__()
        self.image = pygame.Surface((BASKET_WIDTH, BASKET_HEIGHT))
        self.image.fill(BLUE)
        self.rect = self.image.get_rect()
        self.rect.x = (SCREEN_WIDTH - BASKET_WIDTH) // 2
        self.rect.y = SCREEN_HEIGHT - BASKET_HEIGHT - 10
        self.speed = 7
        self.dx = 0

    def update(self):
        self.rect.x += self.dx
        if self.rect.left < 0:
            self.rect.left = 0
        if self.rect.right > SCREEN_WIDTH:
            self.rect.right = SCREEN_WIDTH


class FallingObject(pygame.sprite.Sprite):
    def __init__(self):
        super().__init__()
        self.is_star = random.choice([True, False])
        self.image = pygame.Surface((STAR_SIZE, STAR_SIZE))
        if self.is_star:
            self.image.fill(YELLOW)
            self.score_value = SCORE_PER_STAR
        else:
            self.image.fill(RED)
            self.score_value = -SCORE_PENALTY
        self.rect = self.image.get_rect()
        self.rect.x = random.randint(0, SCREEN_WIDTH - STAR_SIZE)
        self.rect.y = -STAR_SIZE
        self.speed = random.choice([STAR_SPEED, STAR_SPEED + 1])

    def update(self):
        self.rect.y += self.speed


basket = Basket()
falling_objects = pygame.sprite.Group()

score = 0
game_over = False
object_timer = 0
object_interval = 60

clock = pygame.time.Clock()
running = True

while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_LEFT or event.key == pygame.K_a:
                basket.dx = -basket.speed
            elif event.key == pygame.K_RIGHT or event.key == pygame.K_d:
                basket.dx = basket.speed
            elif event.key == pygame.K_SPACE and game_over:
                score = 0
                game_over = False
                falling_objects.empty()
                basket.rect.x = (SCREEN_WIDTH - BASKET_WIDTH) // 2

    basket.update()

    object_timer += 1
    if object_timer >= object_interval:
        falling_objects.add(FallingObject())
        object_timer = 0
        if object_interval > 20:
            object_interval -= 1

    for obj in falling_objects:
        obj.update()

    hits = pygame.sprite.spritecollide(basket, falling_objects, True)
    for hit in hits:
        score += hit.score_value
        if hit.score_value < 0:
            game_over = True

    falling_objects.remove(obj for obj in falling_objects if obj.rect.top > SCREEN_HEIGHT)

    screen.fill(BLACK)
    for obj in falling_objects:
        screen.blit(obj.image, obj.rect.topleft)
    screen.blit(basket.image, basket.rect.topleft)
    score_text = font.render(f"Score: {score}", True, WHITE)
    screen.blit(score_text, (10, 10))
    if game_over:
        over_text = big_font.render("GAME OVER", True, RED)
        screen.blit(over_text, (SCREEN_WIDTH // 2 - over_text.get_width() // 2, SCREEN_HEIGHT // 2 - 50))

    pygame.display.flip()
    clock.tick(60)

pygame.quit()
sys.exit()
"""

DIAGNOSIS_TURN = "Inspect catch_the_stars.py and identify why the left and right movement buttons do not work and why star catching breaks. Diagnose only; do not edit the file."


@dataclass(frozen=True)
class DiagnosisSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    assistant_text: str
    status_history: list[str]
    ui_event_count: int
    verification_block_seen: bool
    escalated: bool


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")


def _seed_script_fixture(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "catch_the_stars.py").write_text(BROKEN_SCRIPT, encoding="utf-8")


def _turn_passed(assistant_text: str, verification_block_seen: bool, escalated: bool, timed_out: bool) -> bool:
    if timed_out:
        return False
    text = assistant_text or ""
    text_l = text.lower()
    mentions_input_bug = any(token in text_l for token in ("dx", "key release", "keyup", "never resets", "left", "right"))
    mentions_game_bug = any(
        token in text_l
        for token in (
            "collision",
            "spritecollide",
            "generator",
            "remove(",
            "off screen",
            "cleanup",
            "star",
            "basket.rect",
            "topleft",
            "reset",
        )
    )
    explanation_cues = any(token in text_l for token in ("because", "issue", "bug", "problem", "root cause", "causing", "never", "uses"))
    code_dump_like = text.strip().startswith("import ") and text.count("\n") > 25
    stale_false_diagnosis = any(
        token in text_l
        for token in (
            "ight // 2 - 50",
            "unterminated string",
            "invalid json structure",
            "syntax or logic corruption",
        )
    )
    avoids_false_failure = "requires verified" not in text_l and "engineering support" not in text_l and "failed" not in text_l
    return (
        mentions_input_bug
        and mentions_game_bug
        and explanation_cues
        and avoids_false_failure
        and not code_dump_like
        and not stale_false_diagnosis
        and not verification_block_seen
        and not escalated
    )


def run_smoke(*, timeout: float, keep_data_copy: bool) -> DiagnosisSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    report = DiagnosisSmokeReport(
        ready=False,
        success=False,
        data_dir=str(harness.data_dir),
        kept_data_dir=None,
        assistant_text="",
        status_history=[],
        ui_event_count=0,
        verification_block_seen=False,
        escalated=False,
    )
    try:
        _clear_isolated_chat_memory(harness.data_dir)
        _seed_script_fixture(harness.data_dir / "workspace")
        boot = harness.start()
        result = harness.send_text(DIAGNOSIS_TURN, timeout_s=timeout)
        ui_payloads = [str(event.get("payload") or "") for event in result.ui_events]
        verification_block_seen = any("verification is still missing" in payload.lower() for payload in ui_payloads)
        report = DiagnosisSmokeReport(
            ready=bool(boot.ready),
            success=bool(boot.ready) and _turn_passed(result.assistant_text, verification_block_seen, escalated, result.timed_out),
            data_dir=str(harness.data_dir),
            kept_data_dir=None,
            assistant_text=result.assistant_text,
            status_history=list(result.status_history),
            ui_event_count=len(result.ui_events),
            verification_block_seen=verification_block_seen,
            escalated=escalated,
        )
    finally:
        harness.close()
    return DiagnosisSmokeReport(
        ready=report.ready,
        success=report.success,
        data_dir=report.data_dir,
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        assistant_text=report.assistant_text,
        status_history=report.status_history,
        ui_event_count=report.ui_event_count,
        verification_block_seen=report.verification_block_seen,
        escalated=report.escalated,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a catch_the_stars diagnosis-only smoke through the isolated Piper harness.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-turn timeout in seconds.")
    parser.add_argument("--keep-data-copy", action="store_true", help="Preserve the isolated data copy for inspection.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    _configure_stdio()
    args = build_parser().parse_args()
    report = run_smoke(timeout=args.timeout, keep_data_copy=args.keep_data_copy)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"READY: {report.ready}")
        print(f"SUCCESS: {report.success}")
        print(f"DATA_DIR: {report.data_dir}")
        if report.kept_data_dir:
            print(f"KEPT_DATA_DIR: {report.kept_data_dir}")
        print(f"assistant={report.assistant_text}")
        print(f"verification_block_seen={report.verification_block_seen}")
        print(f"escalated={report.escalated}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
