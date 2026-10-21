// src/perflab/ResourceState.tsx
//
// The shared honesty boundary for card- and section-shaped surfaces. It owns
// branch SELECTION and the standard notice presentation; screens supply copy,
// an optional action, an optional loading skeleton, and the success renderer.
//
// Branch order is fixed and not negotiable per screen:
//   guest → loading → error → empty → success
//
// A screen physically cannot forget the guest branch or render an error as an
// empty state, because it never selects a branch itself. Inline controls and
// overlays (GoalChips, MacrocycleCreateModal, the Simulator body) are card-
// shaped in neither layout nor semantics; those consume the SAME resource type
// via an exhaustive `switch (resource.status)` instead — one contract, two
// sanctioned consumers.
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Card } from "./ui";
import {
  assertNever,
  selectResourceBranch,
  type AuthedResource,
  type RefreshState,
  type ResourceError,
} from "./resource";
import { isStateUnavailable } from "./provenance";

export interface NoticeAction {
  label: string;
  onClick: () => void;
  primary?: boolean;
}

export interface NoticeContent {
  title?: string;
  body?: ReactNode;
  action?: NoticeAction;
}

/**
 * How much room the notice occupies. These are the three weights the screens
 * actually use today — a full-viewport gate, an in-flow placeholder box, and a
 * one-line note inside an existing card.
 */
export type ResourceStateVariant = "screen" | "box" | "note";

export interface ResourceStateProps<T> {
  resource: AuthedResource<T>;
  /** Editorial reading of successful data. Never consulted off the success branch. */
  isEmpty?: (data: T) => boolean;
  variant?: ResourceStateVariant;
  guest: NoticeContent;
  empty: NoticeContent;
  /** Copy for a failure with no usable payload. The error message is appended as the body when omitted. */
  error?: NoticeContent;
  /** Custom skeleton. Without one, the standard loading notice renders. */
  loading?: ReactNode;
  loadingContent?: NoticeContent;
  /** Shown above children when a payload is on screen but its refresh failed. */
  staleLabel?: string;
  icon?: ReactNode;
  className?: string;
  children: (data: T, refresh: RefreshState<ResourceError>) => ReactNode;
}

const DEFAULT_STALE_LABEL = "Couldn't refresh — showing your last loaded data.";

export function ResourceState<T>({
  resource,
  isEmpty,
  variant = "box",
  guest,
  empty,
  error,
  loading,
  loadingContent,
  staleLabel = DEFAULT_STALE_LABEL,
  icon,
  className,
  children,
}: ResourceStateProps<T>) {
  const branch = selectResourceBranch(resource, isEmpty);

  switch (branch.kind) {
    case "guest":
      return <Notice variant={variant} content={guest} icon={icon} className={className} />;

    case "loading":
      return (
        <>
          {loading ?? (
            <Notice
              variant={variant}
              content={loadingContent ?? { title: "Loading…" }}
              icon={icon}
              className={className}
              live="status"
            />
          )}
        </>
      );

    case "error":
      // INT-15 2C: a coded "state cannot be shown" refusal overrides the screen's retry copy.
      // It is not a transient load failure, and no retry or action of the athlete's fixes it.
      if (isStateUnavailable(branch.error)) {
        return (
          <Notice
            variant={variant}
            tone="error"
            content={{ title: "Training state unavailable", body: branch.error.message }}
            icon={icon}
            className={className}
            live="alert"
          />
        );
      }
      return (
        <Notice
          variant={variant}
          tone="error"
          content={{
            title: error?.title ?? "Couldn't load this",
            body: error?.body ?? branch.error.message,
            action: error?.action,
          }}
          icon={icon}
          className={className}
          live="alert"
        />
      );

    case "empty":
      return <Notice variant={variant} content={empty} icon={icon} className={className} />;

    case "success":
      return (
        <>
          {branch.refresh.status === "error" && (
            <StaleBanner label={staleLabel} message={branch.refresh.error.message} />
          )}
          {children(branch.data, branch.refresh)}
        </>
      );

    default:
      return assertNever(branch);
  }
}

function StaleBanner({ label, message }: { label: string; message: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      title={message}
      className="mb-3 rounded-[10px] border border-hot/[0.3] bg-hot/[0.05] px-3 py-2 text-[12px] font-medium leading-[1.5] text-mute"
    >
      {label}
    </div>
  );
}

function Notice({
  variant,
  content,
  tone = "neutral",
  icon,
  className,
  live,
}: {
  variant: ResourceStateVariant;
  content: NoticeContent;
  tone?: "neutral" | "error";
  icon?: ReactNode;
  className?: string;
  live?: "status" | "alert";
}) {
  const liveProps =
    live === "alert"
      ? ({ role: "alert" } as const)
      : live === "status"
        ? ({ role: "status", "aria-live": "polite" } as const)
        : {};

  if (variant === "note") {
    return (
      <div
        {...liveProps}
        className={cn("text-[13px] font-medium leading-[1.5]", tone === "error" ? "text-hot" : "text-mute", className)}
      >
        {content.title && !content.body ? content.title : content.body}
        {content.action && <NoticeButton action={content.action} className="ml-2" />}
      </div>
    );
  }

  if (variant === "box") {
    return (
      <div
        {...liveProps}
        className={cn(
          "flex min-h-[240px] flex-col items-center justify-center gap-3 rounded-[18px] border p-[30px] text-center",
          tone === "error" ? "border-hot/[0.3] bg-hot/[0.05] text-mute" : "border-dashed border-white/10 text-mute",
          className,
        )}
      >
        {icon}
        {content.title && <div className="text-[15px] font-bold leading-[1.3] text-ink">{content.title}</div>}
        {content.body && (
          <div className="max-w-[360px] text-[12.5px] font-medium leading-[1.5]">{content.body}</div>
        )}
        {content.action && <NoticeButton action={content.action} />}
      </div>
    );
  }

  return (
    <section
      {...liveProps}
      className={cn("flex min-h-[70vh] items-center justify-center px-[30px] pb-9 pt-[26px]", className)}
    >
      <Card className="flex max-w-[520px] flex-col items-center gap-4 p-[44px] text-center">
        {icon}
        {content.title && <div className="text-[20px] font-bold leading-[1.2] text-ink">{content.title}</div>}
        {content.body && (
          <div className="max-w-[380px] text-[13.5px] font-medium leading-[1.6] text-mute">{content.body}</div>
        )}
        {content.action && <NoticeButton action={content.action} />}
      </Card>
    </section>
  );
}

function NoticeButton({ action, className }: { action: NoticeAction; className?: string }) {
  return (
    <button
      onClick={action.onClick}
      className={cn(
        "mt-[6px] rounded-[10px] px-5 py-3 text-[13px] font-semibold leading-none",
        action.primary
          ? "bg-gradient-to-r from-ac to-[#a7e36e] text-[#0a0c10]"
          : "border border-white/10 bg-white/[0.04] text-soft",
        className,
      )}
    >
      {action.label}
    </button>
  );
}
