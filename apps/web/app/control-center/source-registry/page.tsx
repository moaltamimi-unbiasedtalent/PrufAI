"use client";

import { useState } from "react";
import { MetadataBrowser } from "../metadata";
import { SourceWorkbench } from "./workbench";

export default function SourcesPage() {
  const [inspect, setInspect] = useState("");
  return (
    <MetadataBrowser
      section="source-registry"
      onInspect={setInspect}
      workbench={<SourceWorkbench inspect={inspect} />}
    />
  );
}
