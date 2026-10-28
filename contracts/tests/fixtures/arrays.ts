import type {
  CrossReview, ReviewFinding, IntegrityFact, EvidenceRef, TreeResponse, TreeFile, WorkspacePath,
  ChangeFacts, ApproveChange, ChangeResolved, NodeBehaviorDiff, StartAgent,
} from "../../generated/contracts";

export function acceptsArrays(
  findings: ReviewFinding[], evidence: EvidenceRef[], directories: WorkspacePath[], files: TreeFile[],
) {
  const review: Pick<CrossReview, "findings"> = { findings };
  const fact: Pick<IntegrityFact, "evidence"> = { evidence };
  const tree: Pick<TreeResponse, "directories" | "files"> = { directories, files };
  return { review, fact, tree };
}

export function acceptsBoundedArrays(
  summary: string[],
  cards: NonNullable<ChangeFacts["review_cards"]>[number][],
  answers: NonNullable<ApproveChange["answers"]>[number][],
  resolvedAnswers: NonNullable<ChangeResolved["answers"]>[number][],
  rows: NodeBehaviorDiff["rows"][number][],
  examples: NonNullable<StartAgent["examples"]>[number][],
) {
  const facts: Pick<ChangeFacts, "summary" | "review_cards"> = { summary, review_cards: cards };
  const decision: Pick<ApproveChange, "answers"> = { answers };
  const resolution: Pick<ChangeResolved, "answers"> = { answers: resolvedAnswers };
  const diff: Pick<NodeBehaviorDiff, "rows"> = { rows };
  const start: Pick<StartAgent, "examples"> = { examples };
  return { facts, decision, resolution, diff, start };
}
