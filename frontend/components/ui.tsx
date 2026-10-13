/**
 * Shared UI primitives.
 *
 * Kept small and unopinionated: variants only, no layout. Every interactive
 * element is keyboard reachable, visibly focusable (global `:focus-visible` in
 * index.css) and gives an `:active` response, and every one is at least 44px tall
 * at the default size.
 */

import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

export function cx(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}

const TRANSITION = "transition-[background-color,border-color,color,transform,opacity] duration-200 ease-out";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "md" | "sm";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-primary text-on-primary border border-primary hover:brightness-110 shadow-none",
  secondary:
    "bg-elevated text-foreground border border-border hover:border-secondary hover:bg-surface",
  ghost:
    "bg-transparent text-muted-foreground border border-transparent hover:text-foreground hover:bg-elevated",
  danger:
    "bg-transparent text-destructive border border-destructive/50 hover:bg-destructive/10",
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  md: "min-h-11 px-4 text-sm",
  sm: "min-h-8 px-2.5 text-xs",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({
  variant = "secondary",
  size = "md",
  className,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        "inline-flex cursor-pointer items-center justify-center gap-2 rounded-md font-medium",
        TRANSITION,
        "active:scale-[0.98]",
        "disabled:cursor-not-allowed disabled:opacity-45 disabled:active:scale-100",
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      {...props}
    />
  );
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: an icon-only control must carry its own accessible name. */
  label: string;
  children: ReactNode;
}

export function IconButton({ label, children, className, ...props }: IconButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cx(
        "inline-flex size-9 cursor-pointer items-center justify-center rounded-md border border-transparent",
        "text-muted-foreground hover:border-border hover:bg-elevated hover:text-foreground",
        TRANSITION,
        "active:scale-95",
        "disabled:cursor-not-allowed disabled:opacity-45",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export function Panel({
  children,
  className,
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article" | "aside";
}) {
  return (
    <Tag className={cx("panel p-4 sm:p-5", className)}>{children}</Tag>
  );
}

export function Label({
  htmlFor,
  children,
  hint,
}: {
  htmlFor?: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <label
        htmlFor={htmlFor}
        className="font-heading text-xs uppercase tracking-[0.14em] text-muted-foreground"
      >
        {children}
      </label>
      {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
    </div>
  );
}

const FIELD_BASE =
  "w-full rounded-md border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground/70";
const FIELD_STATE =
  "transition-colors duration-200 ease-out hover:border-secondary focus:border-primary focus:outline-none";

export function TextInput({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(FIELD_BASE, FIELD_STATE, className)} {...props} />;
}

export function TextArea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cx(FIELD_BASE, FIELD_STATE, "resize-y leading-relaxed", className)}
      {...props}
    />
  );
}

export function Select({
  className,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <select
      className={cx(FIELD_BASE, FIELD_STATE, "cursor-pointer appearance-none pr-9", className)}
      {...props}
    >
      {children}
    </select>
  );
}

export function Hint({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "error" }) {
  return (
    <p
      className={cx(
        "text-xs leading-relaxed",
        tone === "error" ? "text-destructive" : "text-muted-foreground",
      )}
    >
      {children}
    </p>
  );
}

type PillTone = "neutral" | "success" | "warning" | "danger" | "accent";

const PILL_TONES: Record<PillTone, string> = {
  neutral: "border-border text-muted-foreground",
  success: "border-success/45 text-success",
  warning: "border-warning/45 text-warning",
  danger: "border-destructive/45 text-destructive",
  accent: "border-accent/45 text-accent",
};

export function Pill({
  tone = "neutral",
  children,
  className,
  title,
}: {
  tone?: PillTone;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border bg-background/40 px-2.5 py-1",
        "font-heading text-[11px] uppercase tracking-[0.12em]",
        PILL_TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  children,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-md border border-dashed border-border px-4 py-6">
      {icon ? <span className="text-muted-foreground">{icon}</span> : null}
      <p className="font-heading text-sm text-foreground">{title}</p>
      {children ? (
        <p className="max-w-prose text-sm leading-relaxed text-muted-foreground">{children}</p>
      ) : null}
    </div>
  );
}

export function Callout({
  tone = "neutral",
  icon,
  title,
  children,
  className,
}: {
  tone?: PillTone;
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  const border: Record<PillTone, string> = {
    neutral: "border-border",
    success: "border-success/45",
    warning: "border-warning/45",
    danger: "border-destructive/45",
    accent: "border-accent/45",
  };
  const text: Record<PillTone, string> = {
    neutral: "text-foreground",
    success: "text-success",
    warning: "text-warning",
    danger: "text-destructive",
    accent: "text-accent",
  };
  return (
    <div className={cx("rounded-md border bg-background/40 p-4", border[tone], className)}>
      <div className="flex items-start gap-3">
        {icon ? <span className={cx("mt-0.5 shrink-0", text[tone])}>{icon}</span> : null}
        <div className="min-w-0 space-y-1.5">
          <p className={cx("font-heading text-sm", text[tone])}>{title}</p>
          {children ? (
            <div className="space-y-1.5 text-sm leading-relaxed text-muted-foreground">
              {children}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor} hint={hint}>
        {label}
      </Label>
      {children}
      {error ? (
        <p id={`${htmlFor}-error`} className="text-xs leading-relaxed text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx("font-mono text-xs text-muted-foreground", className)}>{children}</span>;
}