/** 显示格式化：所有里程数字统一保留一位小数。 */
export function fmtKm(km: number): string {
  return String(Math.round(km * 10) / 10)
}
