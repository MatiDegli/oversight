import type {
  CrossReview, ReviewFinding, IntegrityFact, EvidenceRef, TreeResponse, TreeFile, WorkspacePath,
} from "../../generated/contracts";

export function acceptsArrays(
  findings: ReviewFinding[], evidence: EvidenceRef[], directories: WorkspacePath[], files: TreeFile[],
) {
  const review: Pick<CrossReview, "findings"> = { findings };
  const fact: Pick<IntegrityFact, "evidence"> = { evidence };
  const tree: Pick<TreeResponse, "directories" | "files"> = { directories, files };
  return { review, fact, tree };
}
