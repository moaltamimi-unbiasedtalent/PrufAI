import { ControlCentreShell } from "./shell";

export default function ControlCentreLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <ControlCentreShell>{children}</ControlCentreShell>;
}
