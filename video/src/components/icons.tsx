import React from "react";

type P = { size?: number; color?: string; stroke?: number };
const base = (size: number, color: string, stroke: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: color,
  strokeWidth: stroke,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
});

export const IconPipeline: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <path d="M3 20h18" />
    <rect x="5" y="11" width="3" height="7" rx="1" />
    <rect x="10.5" y="7" width="3" height="11" rx="1" />
    <rect x="16" y="4" width="3" height="14" rx="1" />
  </svg>
);

export const IconTicket: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <path d="M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4z" />
    <path d="M9 9h6M9 13h4" />
  </svg>
);

export const IconAlert: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <path d="M12 3 2.5 20h19z" />
    <path d="M12 10v4M12 17.2v.1" />
  </svg>
);

export const IconArrowRight: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <path d="M4 12h15M14 6l6 6-6 6" />
  </svg>
);

export const IconCheck: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 2 }) => (
  <svg {...base(size, color, stroke)}>
    <path d="M4 12.5 9.5 18 20 6.5" />
  </svg>
);

export const IconUser: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" />
  </svg>
);

export const IconShield: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <path d="M12 3 4.5 6v5.5c0 4.5 3.2 8 7.5 9.5 4.3-1.5 7.5-5 7.5-9.5V6z" />
    <path d="m8.8 12 2.2 2.2 4.4-4.6" />
  </svg>
);

export const IconKeyOff: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <circle cx="8" cy="15" r="4" />
    <path d="m11 12 8-8M16 7l2 2M3 3l18 18" />
  </svg>
);

export const IconTrail: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <rect x="4" y="3" width="16" height="18" rx="2" />
    <path d="M8 8h8M8 12h8M8 16h5" />
  </svg>
);

export const IconChat: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <path d="M4 5h16v11H9l-5 4z" />
  </svg>
);

export const IconGroup: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <circle cx="9" cy="8" r="3.2" />
    <circle cx="17" cy="9.5" r="2.5" />
    <path d="M3 20c1-3.5 3.3-5.3 6-5.3s5 1.8 6 5.3M15 14.6c2.6-.3 4.8 1.3 5.8 4.4" />
  </svg>
);

export const IconSpark: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <path d="M12 3c.6 4.6 2.4 6.4 7 7-4.6.6-6.4 2.4-7 7-.6-4.6-2.4-6.4-7-7 4.6-.6 6.4-2.4 7-7z" />
    <path d="M19 15.5c.3 1.8 1 2.5 2.5 2.8-1.5.3-2.2 1-2.5 2.7-.3-1.7-1-2.4-2.5-2.7 1.5-.3 2.2-1 2.5-2.8z" />
  </svg>
);

export const IconClock: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3.5 2" />
  </svg>
);

export const IconGateway: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <rect x="3" y="4" width="18" height="16" rx="3" />
    <path d="M3 9h18M8 14h3M14 14h2" />
  </svg>
);

export const IconBot: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <rect x="4" y="8" width="16" height="11" rx="3" />
    <path d="M12 4v4M9 13v1M15 13v1M2 13v2M22 13v2" />
  </svg>
);

export const IconGlobe: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z" />
  </svg>
);

export const IconPlug: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4" />
  </svg>
);

export const IconWrench: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <path d="M14.7 6.3a4 4 0 0 0-5.4 5.2l-5.8 5.8a1.8 1.8 0 0 0 2.5 2.5l5.8-5.8a4 4 0 0 0 5.2-5.4l-2.5 2.5-2.3-.6-.6-2.3z" />
  </svg>
);

export const IconDatabase: React.FC<P> = ({ size = 48, color = "currentColor", stroke = 1.7 }) => (
  <svg {...base(size, color, stroke)}>
    <ellipse cx="12" cy="6" rx="7" ry="3" />
    <path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" />
  </svg>
);
