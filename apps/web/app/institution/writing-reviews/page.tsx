import Link from "next/link";

const reviews = [
  {
    id: "review-001",
    attempt: "Attempt ••••42",
    task: "B1 Writing",
    status: "REVIEW_REQUIRED",
    age: "18 min",
  },
];

export default function WritingReviewQueuePage() {
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution
      </p>
      <h1>Writing review queue</h1>
      <p>
        Review proposals are private to authorised institution reviewers.
        Candidate writing is not shown in this list.
      </p>
      <table>
        <caption className="sr-only">Pending writing assessments</caption>
        <thead>
          <tr>
            <th scope="col">Assessment</th>
            <th scope="col">Attempt</th>
            <th scope="col">Task</th>
            <th scope="col">Status</th>
            <th scope="col">Age</th>
          </tr>
        </thead>
        <tbody>
          {reviews.map((review) => (
            <tr key={review.id}>
              <td>
                <Link href={`/institution/writing-reviews/${review.id}`}>
                  {review.id}
                </Link>
              </td>
              <td>{review.attempt}</td>
              <td>{review.task}</td>
              <td>{review.status}</td>
              <td>{review.age}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
