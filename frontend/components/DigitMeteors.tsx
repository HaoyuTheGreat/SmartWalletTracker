"use client";

/**
 * DigitMeteors — fixed full-viewport canvas that renders streams of digits
 * shooting diagonally across the screen with fading trails. Each "meteor"
 * is a short string of characters (digits + financial symbols) moving
 * southeast on a black background, evoking a Matrix-meets-trading-terminal
 * vibe for the HomePage splash.
 *
 * Implementation notes:
 *   - Single <canvas> sized to window, position:fixed behind all content.
 *   - 25 meteors at a time; each is respawned when it exits the viewport.
 *   - Trail = drawing each character at offset positions along the velocity
 *     vector, with opacity fading toward the tail.
 *   - Background fade is a per-frame semi-transparent black overlay (instead
 *     of clearing), so glyph residue creates a subtle "afterimage" trail.
 *   - devicePixelRatio scaling keeps glyphs crisp on Retina displays.
 */

import { useEffect, useRef } from "react";

const CHAR_POOL = "0123456789%$.+-";
const METEOR_COUNT = 50;
const TRAIL_LENGTH = 12;
const GLYPH_SIZE_PX = 18;
// Fixed spacing along the velocity vector — keeps glyphs visually separated
// regardless of meteor speed (previously they overlapped at slow speeds).
const GLYPH_SPACING_PX = 22;

interface Meteor {
  x: number;
  y: number;
  vx: number;
  vy: number;
  // Unit direction vector — used to space glyphs along the trail at a fixed
  // pixel offset independent of speed.
  dirX: number;
  dirY: number;
  chars: string[];
  age: number;
  maxAge: number;
}

function randomChar(): string {
  return CHAR_POOL[Math.floor(Math.random() * CHAR_POOL.length)];
}

function createMeteor(width: number): Meteor {
  // Spawn slightly above and to the left of the viewport so the head enters smoothly.
  const startX = Math.random() * (width + 200) - 100;
  const startY = -50;
  // Southeast direction: ~45° ± 10° from horizontal.
  const angle = Math.PI / 4 + (Math.random() - 0.5) * 0.35;
  const speed = 2 + Math.random() * 2.5;
  const dirX = Math.cos(angle);
  const dirY = Math.sin(angle);
  return {
    x: startX,
    y: startY,
    vx: dirX * speed,
    vy: dirY * speed,
    dirX,
    dirY,
    chars: Array.from({ length: TRAIL_LENGTH }, randomChar),
    age: 0,
    maxAge: 300 + Math.random() * 250,
  };
}

export function DigitMeteors() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let width = window.innerWidth;
    let height = window.innerHeight;
    let dpr = window.devicePixelRatio || 1;

    const resize = () => {
      width = window.innerWidth;
      height = window.innerHeight;
      dpr = window.devicePixelRatio || 1;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.scale(dpr, dpr);
      ctx.font = `${GLYPH_SIZE_PX}px ui-monospace, SFMono-Regular, Menlo, monospace`;
    };
    resize();
    window.addEventListener("resize", resize);

    const meteors: Meteor[] = Array.from({ length: METEOR_COUNT }, () =>
      createMeteor(width),
    );

    let rafId = 0;

    const tick = () => {
      // Fade the previous frame instead of clearing — leaves a subtle afterimage.
      // Higher alpha = faster fade = more discrete glyphs (less smearing).
      ctx.fillStyle = "rgba(0, 0, 0, 0.35)";
      ctx.fillRect(0, 0, width, height);

      for (let i = 0; i < meteors.length; i++) {
        const m = meteors[i];

        // Draw the trail: each char placed at fixed pixel intervals upstream
        // along the unit direction vector, so glyphs stay visually separated
        // regardless of meteor speed.
        for (let j = 0; j < m.chars.length; j++) {
          const trailX = m.x - m.dirX * GLYPH_SPACING_PX * j;
          const trailY = m.y - m.dirY * GLYPH_SPACING_PX * j;
          const fade = 1 - j / TRAIL_LENGTH;

          if (j === 0) {
            // Bright head — warm cream-gold.
            ctx.fillStyle = `rgba(255, 224, 150, ${fade})`;
          } else {
            // Dark gold trail (goldenrod / amber), fading toward the tail.
            ctx.fillStyle = `rgba(184, 134, 32, ${fade * 0.9})`;
          }
          ctx.fillText(m.chars[j], trailX, trailY);
        }

        m.x += m.vx;
        m.y += m.vy;
        m.age++;

        // Respawn when off-screen or aged out.
        if (
          m.x > width + 200 ||
          m.y > height + 200 ||
          m.age > m.maxAge
        ) {
          meteors[i] = createMeteor(width);
        }
      }

      rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 -z-10 bg-black"
      aria-hidden
    />
  );
}
