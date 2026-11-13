"use client";

import { useState } from "react";
import { pilotActive } from "../../../lib/access";
import { useAuth } from "../../../lib/auth";
import { MetadataBrowser } from "../metadata";
import { SourceWorkbench } from "./workbench";

export default function SourcesPage() {
  const restricted = pilotActive(useAuth().user);
  const [inspect, setInspect] = useState("");
  return (
    <MetadataBrowser
      section="source-registry"
      onInspect={restricted ? undefined : setInspect}
      workbench={restricted ? undefined : <SourceWorkbench inspect={inspect} />}
    />
  );
}
