import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/editor";
import "monaco-editor/features/register.all";
import "monaco-editor/languages/definitions/python/register";
import "monaco-editor/languages/definitions/yaml/register";
import "monaco-editor/languages/definitions/markdown/register";
import "monaco-editor/languages/definitions/shell/register";
import "monaco-editor/languages/definitions/ini/register";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import "./monaco.css";

// Definitions only: all languages share the editor worker, without validation or diagnostics.
self.MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });

export { monaco };
