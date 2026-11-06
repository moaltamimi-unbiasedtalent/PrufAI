import Link from "next/link";
import { MetadataBrowser } from "../metadata";

export default function QuestionsPage() {
  return (
    <>
      <MetadataBrowser section="question-bank" />
      <p>
        <Link href="/control-center/standards/lid/editorial">
          Original Berlin LiD editorial set
        </Link>
      </p>
    </>
  );
}
