"use client";
import { use } from "react";
import { SOON_TITLES } from "@/components/nav";

export default function Soon({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const title = SOON_TITLES[slug] ?? "This screen";
  return (
    <main className="page">
      <h1 className="disp" style={{ margin: 0, fontSize: 36 }}>{title}</h1>
      <div className="card card-pad" style={{ maxWidth: 640 }}>
        <p style={{ margin: 0, fontSize: 16, lineHeight: 1.55 }}>
          The API for this module is built and tested, and the screen is next on the roadmap. Until it ships, the same data is
          available through the API (<span className="mono">/v1</span>).
        </p>
      </div>
    </main>
  );
}
