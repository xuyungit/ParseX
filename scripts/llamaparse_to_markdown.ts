#!/usr/bin/env node

import { createReadStream } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import LlamaCloud from "@llamaindex/llama-cloud";

type Args = {
  input: string;
  output: string;
  metadata: string;
  tier: string;
  version: string;
  customPrompt?: string;
};

function parseArgs(argv: string[]): Args {
  const values: Record<string, string> = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) {
      continue;
    }
    const key = token.slice(2);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`Missing value for --${key}`);
    }
    values[key] = value;
    index += 1;
  }

  if (!values.input || !values.output || !values.metadata) {
    throw new Error(
      "Usage: llamaparse_to_markdown.ts --input <file> --output <file> --metadata <file> [--tier agentic] [--version latest] [--custom-prompt <text>]"
    );
  }

  return {
    input: values.input,
    output: values.output,
    metadata: values.metadata,
    tier: values.tier ?? "agentic",
    version: values.version ?? "latest",
    customPrompt: values["custom-prompt"],
  };
}

async function main() {
  if (!process.env.LLAMA_CLOUD_API_KEY) {
    throw new Error("LLAMA_CLOUD_API_KEY is not set.");
  }

  const args = parseArgs(process.argv.slice(2));
  const client = new LlamaCloud({
    apiKey: process.env.LLAMA_CLOUD_API_KEY,
  });

  const uploaded = await client.files.create({
    file: createReadStream(args.input),
    purpose: "parse",
  });

  const parseParams: Record<string, unknown> = {
    tier: args.tier,
    version: args.version,
    file_id: uploaded.id,
    expand: ["markdown_full", "text_full"],
  };

  if (args.customPrompt) {
    parseParams.agentic_options = {
      custom_prompt: args.customPrompt,
    };
  }

  const result = await client.parsing.parse(parseParams as never);
  const markdown = result.markdown_full ?? result.text_full ?? "";

  await mkdir(path.dirname(args.output), { recursive: true });
  await mkdir(path.dirname(args.metadata), { recursive: true });
  await writeFile(args.output, markdown, "utf-8");
  await writeFile(
    args.metadata,
    JSON.stringify(
      {
        tool: "llamaparse",
        input_path: path.resolve(args.input),
        output_path: path.resolve(args.output),
        tier: args.tier,
        version: args.version,
        file_id: uploaded.id,
        parse_job_id: result.id ?? null,
        status: result.status ?? null,
        markdown_chars: markdown.length,
      },
      null,
      2
    ),
    "utf-8"
  );
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(message);
  process.exit(1);
});
