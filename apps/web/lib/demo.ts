export function developmentDemoEnabled(
  environment: string | undefined,
  flag: string | undefined,
) {
  return environment === "development" && flag === "1";
}
