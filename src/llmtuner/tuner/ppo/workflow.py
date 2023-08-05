# Inspired by:
# https://github.com/lvwerra/trl/blob/main/examples/research_projects/stack_llama/scripts/rl_training.py

import math
from typing import TYPE_CHECKING
from trl import PPOConfig
from torch.optim import AdamW
from typing import Optional, List
from transformers import DataCollatorForSeq2Seq
from transformers.optimization import get_scheduler

from llmtuner.dsets import get_dataset, preprocess_dataset
from llmtuner.extras.ploting import plot_loss
from llmtuner.tuner.core import load_model_and_tokenizer
from llmtuner.tuner.ppo.trainer import PPOPeftTrainer

if TYPE_CHECKING:
    from transformers import Seq2SeqTrainingArguments, TrainerCallback
    from llmtuner.hparams import ModelArguments, DataArguments, FinetuningArguments


def run_ppo(
    model_args: "ModelArguments",
    data_args: "DataArguments",
    training_args: "Seq2SeqTrainingArguments",
    finetuning_args: "FinetuningArguments",
    callbacks: Optional[List["TrainerCallback"]] = None
):
    dataset = get_dataset(model_args, data_args)
    model, tokenizer = load_model_and_tokenizer(model_args, finetuning_args, training_args.do_train, stage="ppo")
    dataset = preprocess_dataset(dataset, tokenizer, data_args, training_args, stage="ppo")
    data_collator = DataCollatorForSeq2Seq(tokenizer=tokenizer, label_pad_token_id=tokenizer.pad_token_id)

    ppo_config = PPOConfig(
        model_name=model_args.model_name_or_path,
        learning_rate=training_args.learning_rate,
        mini_batch_size=training_args.per_device_train_batch_size,
        batch_size=training_args.per_device_train_batch_size * training_args.gradient_accumulation_steps,
        gradient_accumulation_steps=training_args.gradient_accumulation_steps,
        ppo_epochs=1,
        max_grad_norm=training_args.max_grad_norm
    )

    optimizer = AdamW(filter(lambda p: p.requires_grad, model.parameters()), lr=ppo_config.learning_rate)
    total_train_batch_size = \
        training_args.per_device_train_batch_size * training_args.gradient_accumulation_steps * training_args.world_size
    lr_scheduler = get_scheduler(
        training_args.lr_scheduler_type,
        optimizer=optimizer,
        num_warmup_steps=training_args.warmup_steps,
        num_training_steps=(training_args.num_train_epochs * math.ceil(len(dataset) / total_train_batch_size))
    )

    # Initialize our Trainer
    ppo_trainer = PPOPeftTrainer(
        training_args=training_args,
        finetuning_args=finetuning_args,
        callbacks=callbacks,
        config=ppo_config,
        model=model,
        ref_model=None,
        tokenizer=tokenizer,
        dataset=dataset,
        data_collator=data_collator,
        optimizer=optimizer,
        lr_scheduler=lr_scheduler
    )

    ppo_trainer.ppo_train(max_target_length=data_args.max_target_length)
    ppo_trainer.save_model()
    ppo_trainer.save_state() # must be after save_model
    if ppo_trainer.is_world_process_zero() and model_args.plot_loss:
        plot_loss(training_args.output_dir, keys=["loss", "reward"])
