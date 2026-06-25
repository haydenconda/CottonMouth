/**
 * CottonMouth mark — a coiled pit-viper drawn as an inward spiral of
 * decreasing semicircles, with a head and a forked tongue. Inherits
 * `currentColor`, so size/color it via Tailwind text utilities.
 */
export function CottonmouthLogo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {/* coiled body: stacked semicircles spiralling toward the centre */}
      <path d="M3 13 A9 9 0 0 1 21 13 A8 8 0 0 1 5 13 A7 7 0 0 1 19 13 A6 6 0 0 1 7 13 A5 5 0 0 1 17 13 A3.5 3.5 0 0 1 10 13" />
      {/* head */}
      <circle cx="3" cy="13" r="1.6" fill="currentColor" stroke="none" />
      {/* forked tongue */}
      <path d="M3 13 L0.8 11.7 M3 13 L0.8 14.3" strokeWidth={1.3} />
    </svg>
  );
}
