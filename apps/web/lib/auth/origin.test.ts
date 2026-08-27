import { describe, expect, it } from "vitest";

import { isSameOriginFormPost } from "@/lib/auth/origin";

function request(headers: Record<string, string>) {
  return new Request("http://internal-next-origin:3000/api/session", {
    method: "POST",
    headers,
  });
}

describe("same-origin form validation", () => {
  it("accepts a browser post when Origin and Host match behind reconstructed Next URLs", () => {
    expect(
      isSameOriginFormPost(
        request({
          host: "127.0.0.1:3001",
          origin: "http://127.0.0.1:3001",
          "sec-fetch-site": "same-origin",
        }),
      ),
    ).toBe(true);
  });

  it("rejects cross-site, missing-origin, and mismatched-host posts", () => {
    expect(
      isSameOriginFormPost(
        request({
          host: "127.0.0.1:3001",
          origin: "https://attacker.example",
          "sec-fetch-site": "cross-site",
        }),
      ),
    ).toBe(false);
    expect(isSameOriginFormPost(request({ host: "127.0.0.1:3001" }))).toBe(false);
    expect(
      isSameOriginFormPost(
        request({
          host: "127.0.0.1:3001",
          origin: "http://localhost:3001",
          "sec-fetch-site": "same-origin",
        }),
      ),
    ).toBe(false);
  });
});
