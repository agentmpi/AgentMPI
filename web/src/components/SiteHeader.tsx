import Link from "next/link";

const links = [
  { href: "/", label: "Overview" },
  { href: "/paper", label: "Paper" },
  { href: "/spec", label: "Spec" },
  { href: "/experiments", label: "Experiments" },
];

export function SiteHeader() {
  return (
    <header className="border-b border-rule bg-card/80 backdrop-blur sticky top-0 z-20">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 md:px-6">
        <Link href="/" className="text-ink no-underline">
          <span className="font-semibold tracking-tight">AgentMPI</span>
          <span className="ml-2 hidden text-sm text-muted sm:inline">1.0</span>
        </Link>
        <nav className="flex flex-wrap items-center gap-3 text-sm md:gap-5">
          {links.map((l) => (
            <Link key={l.href} href={l.href} className="text-ink no-underline hover:text-accent">
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
