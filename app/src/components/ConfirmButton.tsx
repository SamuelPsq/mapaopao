import { useState, type ComponentProps, type ReactNode } from "react"
import type { VariantProps } from "class-variance-authority"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { Button, buttonVariants } from "@/components/ui/button"

type ButtonProps = ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }

interface ConfirmButtonProps extends Omit<ButtonProps, "onClick"> {
  /** 确认弹窗标题 */
  title: string
  /** 确认弹窗说明（可带当前值摘要） */
  description: ReactNode
  confirmText?: string
  cancelText?: string
  onConfirm: () => void
}

/** 单向按钮防误触：点击先弹确认窗，点「确认」才执行真正动作。 */
export default function ConfirmButton({
  title, description, confirmText = "确认", cancelText = "点错了",
  onConfirm, children, ...btn
}: ConfirmButtonProps) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button {...btn} onClick={() => setOpen(true)}>{children}</Button>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{title}</AlertDialogTitle>
            <AlertDialogDescription>{description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{cancelText}</AlertDialogCancel>
            <AlertDialogAction onClick={() => { setOpen(false); onConfirm() }}>
              {confirmText}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
