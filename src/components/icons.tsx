/**
 * Inline SVG icon set.
 *
 * Generic UI icons only — the project deliberately carries no icon dependency,
 * and emoji are never used as icons. Every icon is `aria-hidden`: the accessible
 * name always comes from the control or text beside it.
 */

import type { ReactNode, SVGProps } from "react";

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "children"> {
  /** Rendered as a `<title>` when the icon stands alone and needs a name. */
  title?: string;
}

function Svg({ title, children, ...props }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      width={16}
      height={16}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      focusable="false"
      {...props}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  );
}

export const IconArrowRight = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Svg>
);

export const IconPlus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const IconClose = (p: IconProps) => (
  <Svg {...p}>
    <path d="M18 6 6 18M6 6l12 12" />
  </Svg>
);

export const IconPause = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 5v14M15 5v14" />
  </Svg>
);

export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 6 9 17l-5-5" />
  </Svg>
);

export const IconAlert = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4 2.5 20h19L12 4Z" />
    <path d="M12 10v4M12 17.5h.01" />
  </Svg>
);

export const IconInfo = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5M12 8h.01" />
  </Svg>
);

export const IconSkip = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 12h14" />
    <path d="M15 8l4 4-4 4" />
  </Svg>
);

export const IconSpinner = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3a9 9 0 1 0 9 9" />
  </Svg>
);

export const IconCopy = (p: IconProps) => (
  <Svg {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M15 5.5A2.5 2.5 0 0 0 12.5 3h-6A3.5 3.5 0 0 0 3 6.5v6A2.5 2.5 0 0 0 5.5 15" />
  </Svg>
);

export const IconDownload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3v12M7 11l5 5 5-5M4 21h16" />
  </Svg>
);

export const IconEdit = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 20h4L20 8a2.8 2.8 0 0 0-4-4L4 16v4Z" />
    <path d="M14.5 5.5 18.5 9.5" />
  </Svg>
);

export const IconLock = (p: IconProps) => (
  <Svg {...p}>
    <rect x="4.5" y="10.5" width="15" height="10" rx="2" />
    <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
  </Svg>
);

export const IconLink = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10 13.5a3.5 3.5 0 0 0 5 0l3.5-3.5a3.5 3.5 0 0 0-5-5L12 6.5" />
    <path d="M14 10.5a3.5 3.5 0 0 0-5 0L5.5 14a3.5 3.5 0 0 0 5 5L12 17.5" />
  </Svg>
);

export const IconText = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 6h14M5 11h14M5 16h9" />
  </Svg>
);

export const IconCode = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 8 5 12l4 4M15 8l4 4-4 4" />
  </Svg>
);

export const IconFile = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 3h7l5 5v13H6V3Z" />
    <path d="M13 3v5h5" />
  </Svg>
);

export const IconMic = (p: IconProps) => (
  <Svg {...p}>
    <rect x="9" y="3" width="6" height="10" rx="3" />
    <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" />
  </Svg>
);

export const IconChevron = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 10l4 4 4-4" />
  </Svg>
);

export const IconRefresh = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 11a8 8 0 0 0-14-4.5L4 9" />
    <path d="M4 5v4h4" />
    <path d="M4 13a8 8 0 0 0 14 4.5L20 15" />
    <path d="M20 19v-4h-4" />
  </Svg>
);

export const IconGlobe = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3.5 12h17M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18Z" />
  </Svg>
);

export const SOURCE_ICONS = {
  public_url: IconGlobe,
  text: IconText,
  raw_html: IconCode,
  pdf: IconFile,
  docx: IconFile,
  audio: IconMic,
} as const;
