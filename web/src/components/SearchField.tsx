import { useEffect, useRef } from "react";
import { cx } from "../lib/cx";
import { icons } from "./icons";

export interface SearchFieldProps {
  placeholder?: string;
  showShortcut?: boolean;
  value?: string;
  onChange?: (value: string) => void;
  className?: string;
}

/** Top bar search input with shortcut hint; ⌘K / Ctrl+K focuses it. */
export function SearchField({
  placeholder = "Search",
  showShortcut = true,
  value,
  onChange,
  className,
}: SearchFieldProps) {
  const input = useRef<HTMLInputElement>(null);
  const SearchIcon = icons.ActionSearch;

  useEffect(() => {
    if (!showShortcut) return;
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showShortcut]);

  return (
    <label
      className={cx(
        "flex items-center gap-2 rounded-sm border border-rule bg-raised px-2 font-sans focus-within:border-rule-strong",
        className ?? "h-7 w-[240px] shrink-0",
      )}
    >
      <SearchIcon aria-hidden className="size-[13px] shrink-0 text-faint" />
      <input
        ref={input}
        type="search"
        value={value}
        onChange={(event) => onChange?.(event.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="min-w-0 flex-1 bg-transparent text-xs leading-normal text-text outline-none placeholder:text-faint"
      />
      {showShortcut && <span className="font-mono text-[10px] leading-normal text-faint">⌘K</span>}
    </label>
  );
}
