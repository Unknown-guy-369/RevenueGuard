import { ExternalLink } from "lucide-react";

type PaymentLinkActionProps = {
  action: {
    action_type: string;
    status: string;
    payment_link_url: string | null;
  };
};

/** A link is an operator handoff, never proof of recovery. */
export function PaymentLinkAction({ action }: PaymentLinkActionProps) {
  if (
    action.action_type !== "CREATE_PAYMENT_LINK" ||
    action.status !== "SUCCEEDED" ||
    action.payment_link_url === null
  ) {
    return null;
  }

  return (
    <a
      className="mt-4 inline-flex items-center gap-2 rounded-lg bg-payment-blue px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-payment-blue/90"
      href={action.payment_link_url}
      rel="noreferrer"
      target="_blank"
    >
      <ExternalLink aria-hidden="true" className="h-4 w-4" />
      Open Razorpay Test payment link
    </a>
  );
}
