declare module "*.jsonl?recorded-replay" {
  const recording: import("./replay-source").RecordedReplay;
  export default recording;
}
