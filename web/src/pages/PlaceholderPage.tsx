export function PlaceholderPage({ title, milestone }: { title: string; milestone: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-rule px-4">
        <h1 className="text-sm leading-normal font-medium text-text">{title}</h1>
      </header>
      <div className="flex flex-1 items-center justify-center">
        <p className="text-xs text-faint">
          {title} arrives in <span className="font-mono text-muted">{milestone}</span>.
        </p>
      </div>
    </div>
  );
}
