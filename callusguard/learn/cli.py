"""CLI for the offline learning/control plane."""

from __future__ import annotations

import argparse
import json
import shlex
import sys

from .artifacts import write_proposal
from .distill import CommandProvider, distill
from .models import EvidencePacket, Intervention, Pattern
from .store import KnowledgeStore


def _store(args) -> KnowledgeStore:
    return KnowledgeStore(args.home)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="callus learn")
    p.add_argument("--home", help="knowledge directory (default: ~/.callusguard/knowledge)")
    sub = p.add_subparsers(dest="action", required=True)

    sub.add_parser("init")

    pa = sub.add_parser("pattern-add")
    pa.add_argument("id")
    pa.add_argument("observation")
    pa.add_argument("--hypothesis")

    sub.add_parser("patterns")

    ia = sub.add_parser("intervention-add")
    ia.add_argument("id")
    ia.add_argument("pattern_id")
    ia.add_argument("kind", choices=sorted({"rule", "skill", "workflow", "tool_fix", "orchestration_change", "no_action"}))
    ia.add_argument("description")
    ia.add_argument("--status", default="proposed")
    ia.add_argument("--result")

    sub.add_parser("interventions")

    d = sub.add_parser("distill")
    d.add_argument("packet", help="EvidencePacket JSON file")
    d.add_argument("--provider-command", required=True,
                   help="command that reads {system,input} JSON on stdin and prints proposal JSON")
    d.add_argument("--out", required=True)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    store = _store(args)
    if args.action == "init":
        store.init()
        print(store.root)
        return 0
    if args.action == "pattern-add":
        store.add_pattern(Pattern(args.id, args.observation, args.hypothesis))
        return 0
    if args.action == "patterns":
        print(json.dumps(store.patterns(), indent=2, sort_keys=True))
        return 0
    if args.action == "intervention-add":
        store.add_intervention(Intervention(
            args.id, args.pattern_id, args.kind, args.description,
            status=args.status, result=args.result))
        return 0
    if args.action == "interventions":
        print(json.dumps(store.interventions(), indent=2, sort_keys=True))
        return 0
    if args.action == "distill":
        raw = json.load(open(args.packet, "r", encoding="utf-8"))
        packet = EvidencePacket(**{k: v for k, v in raw.items() if k != "evidence_refs"})
        proposal = distill(packet, CommandProvider(shlex.split(args.provider_command)))
        write_proposal(proposal, args.out)
        print(args.out)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
