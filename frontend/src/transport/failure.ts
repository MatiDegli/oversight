import { CommandError } from "./commands";

/** Application refusals keep their explanation; HTTP and network failures get product words. */
export function commandFailure(action: string, error: unknown): string {
  if (error instanceof CommandError) {
    if (error.status >= 400 && error.status < 500) {
      if (!/^HTTP\s+\d{3}\b/.test(error.message)) return error.message;
      return `${action} failed: the server could not complete the request. Try again.`;
    }
    return `${action} failed: the server hit an internal error. Try again.`;
  }
  return `${action} did not reach the server. Check the connection and try again.`;
}
