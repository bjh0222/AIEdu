import { canonical, sha256Hex } from "./client";

/** 与后端 digest() 同口径：aiedu.canonical-json.v1（键排序、无空白、UTF-8、SHA-256 小写）。 */
export async function digest(value: unknown): Promise<string> {
  return sha256Hex(canonical(value));
}
