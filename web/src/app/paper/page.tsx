import { readFileSync } from "node:fs";
import { join } from "node:path";

function loadPaper(): string {
  const here = join(process.cwd(), "..", "paper", "agentmpi.md");
  try {
    return readFileSync(here, "utf8");
  } catch {
    return readFileSync(join(process.cwd(), "public", "paper", "agentmpi.md"), "utf8");
  }
}

function render(md: string) {
  const blocks = md.split("\n## ");
  return blocks.map((block, i) => {
    const lines = (i === 0 ? block : "## " + block).split("\n");
    const title = lines[0].replace(/^#+\s/, "");
    const body = lines.slice(1).join("\n").trim();
    return (
      <section key={title} className="mt-10">
        {i === 0 ? (
          <pre className="whitespace-pre-wrap font-sans text-base leading-8">{block}</pre>
        ) : (
          <>
            <h2
              className="text-2xl font-semibold"
              style={{ fontFamily: "var(--font-source-serif), Georgia, serif" }}
            >
              {title}
            </h2>
            <pre className="mt-3 whitespace-pre-wrap font-sans text-base leading-8 text-ink/90">{body}</pre>
          </>
        )}
      </section>
    );
  });
}

export default function PaperPage() {
  const md = loadPaper();
  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 md:px-6">
      <p className="text-xs uppercase tracking-[0.2em] text-accent">First paper · August 2026</p>
      <article className="paper-body">{render(md)}</article>
    </main>
  );
}
