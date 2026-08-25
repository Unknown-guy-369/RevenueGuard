type ArchitectureCardProps = {
  eyebrow: string;
  title: string;
  description: string;
  tag: string;
};

export function ArchitectureCard({ eyebrow, title, description, tag }: ArchitectureCardProps) {
  return (
    <article className="architecture-card">
      <div className="card-meta">
        <span>{eyebrow}</span>
        <span className="card-tag">{tag}</span>
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
    </article>
  );
}
