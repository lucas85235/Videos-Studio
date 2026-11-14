import React, { useId } from 'react';

/** Símbolo do Vulto: busto sem rosto recortado num quadrado âmbar (docs/MARCA.md). */
export const VultoMark: React.FC<{ size?: number; className?: string }> = ({ size = 28, className = '' }) => {
  const clipId = `vulto-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  return (
    <svg
      viewBox="0 0 32 32"
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      className={`shrink-0 ${className}`}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <clipPath id={clipId}>
          <rect width="32" height="32" rx="8" />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`}>
        <rect width="32" height="32" fill="#F5A524" />
        <circle cx="16" cy="13" r="5.5" fill="#0B0B0E" />
        <path d="M5 33C5 24 9.9 19.9 16 19.9S27 24 27 33Z" fill="#0B0B0E" />
      </g>
    </svg>
  );
};

/** Logotipo: símbolo + VULTO (Montserrat 800, espaçamento 0,14 em) + descritor quando há espaço. */
export const VultoLogo: React.FC<{ collapsed?: boolean }> = ({ collapsed = false }) => (
  <span className="flex min-w-0 items-center gap-2">
    <VultoMark size={30} />
    {collapsed ? (
      <span className="sr-only">Vulto</span>
    ) : (
      <span className="flex min-w-0 flex-col leading-none">
        <span className="font-display text-[17px] font-extrabold tracking-[0.14em] text-fg">VULTO</span>
        <span className="mt-1 truncate text-xs text-subtle">estúdio de vídeos sem rosto</span>
      </span>
    )}
  </span>
);
