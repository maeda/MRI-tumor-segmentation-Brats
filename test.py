import numpy as np
import keras
import argparse
import os

from keras.backend import learning_phase

import tf_models
import tensorflow as tf
import tensorflow.compat.v1 as tf

tf.disable_v2_behavior()
from keras.models import Sequential, Model
from keras.layers import (
    Dense,
    Conv3D,
    Dropout,
    Flatten,
    Input,
    concatenate,
    Reshape,
    Lambda,
    Permute,
)
from keras.layers.core import Dense, Dropout, Activation, Reshape
from keras.layers.convolutional import Conv3D, Conv3DTranspose, UpSampling3D
from keras.layers.pooling import AveragePooling3D
from keras.layers import Input
from keras.layers.merge import concatenate
from keras.layers.normalization import BatchNormalization

# from tensorflow.contrib.keras.python.keras.backend import learning_phase

from nibabel import load as load_nii
from sklearn.preprocessing import scale
import matplotlib.pyplot as plt


# SAVE_PATH = 'unet3d_baseline.hdf5'
# OFFSET_W = 16
# OFFSET_H = 16
# OFFSET_C = 4
# HSIZE = 64
# WSIZE = 64
# CSIZE = 16
# batches_h, batches_w, batches_c = (224-HSIZE)/OFFSET_H+1, (224-WSIZE)/OFFSET_W+1, (152 - CSIZE)/OFFSET_C+1


def parse_inputs():
    parser = argparse.ArgumentParser(
        description="Test different nets with 3D data."
    )
    parser.add_argument(
        "-r",
        "--root-path",
        dest="root_path",
        default="./data/MICCAI_BraTS_2019_Data_Training/HGG",
    )
    parser.add_argument(
        "-m", "--model-path", dest="model_path", default="NoneDense-0"
    )
    parser.add_argument(
        "-ow", "--offset-width", dest="offset_w", type=int, default=12
    )
    parser.add_argument(
        "-oh", "--offset-height", dest="offset_h", type=int, default=12
    )
    parser.add_argument(
        "-oc",
        "--offset-channel",
        dest="offset_c",
        nargs="+",
        type=int,
        default=12,
    )
    parser.add_argument(
        "-ws", "--width-size", dest="wsize", type=int, default=38
    )
    parser.add_argument(
        "-hs", "--height-size", dest="hsize", type=int, default=38
    )
    parser.add_argument(
        "-cs", "--channel-size", dest="csize", type=int, default=38
    )
    parser.add_argument(
        "-ps", "--pred-size", dest="psize", type=int, default=12
    )
    parser.add_argument("-gpu", "--gpu", dest="gpu", type=str, default="0")
    parser.add_argument(
        "-mn", "--model_name", dest="model_name", type=str, default="dense24"
    )
    parser.add_argument(
        "-nc", "--correction", dest="correction", type=bool, default=False
    )

    return vars(parser.parse_args())


options = parse_inputs()
os.environ["CUDA_VISIBLE_DEVICES"] = options["gpu"]


def segmentation_loss(y_true, y_pred, n_classes):
    y_true = tf.reshape(y_true, (-1, n_classes))
    y_pred = tf.reshape(y_pred, (-1, n_classes))
    return tf.reduce_mean(
        tf.nn.softmax_cross_entropy_with_logits(labels=y_true, logits=y_pred)
    )


def vox_preprocess(vox):
    vox_shape = vox.shape
    vox = np.reshape(vox, (-1, vox_shape[-1]))
    vox = scale(vox, axis=0)
    return np.reshape(vox, vox_shape)


def one_hot(y, num_classees):
    y_ = np.zeros([len(y), num_classees])
    y_[np.arange(len(y)), y] = 1
    return y_


def dice_coef_np(y_true, y_pred, num_classes):
    """

    :param y_true: sparse labels
    :param y_pred: sparse labels
    :param num_classes: number of classes
    :return:
    """
    y_true = y_true.astype(int)
    y_pred = y_pred.astype(int)
    y_true = y_true.flatten()
    y_true = one_hot(y_true, num_classes)
    y_pred = y_pred.flatten()
    y_pred = one_hot(y_pred, num_classes)
    intersection = np.sum(y_true * y_pred, axis=0)
    union = np.sum(y_true, axis=0) + np.sum(y_pred, axis=0)
    if union[1] <= 0:
        raise ValueError("Imminent division by zero.")
    print("union", union)
    return (2.0 * intersection) / union


def DenseNetUnit3D(x, growth_rate, ksize, n, bn_decay=0.99):
    for i in range(n):
        concat = x
        x = BatchNormalization(center=True, scale=True, momentum=bn_decay)(x)
        x = Activation("relu")(x)
        x = Conv3D(
            filters=growth_rate,
            kernel_size=ksize,
            padding="same",
            kernel_initializer="he_uniform",
            use_bias=False,
        )(x)
        x = concatenate([concat, x])
    return x


def DenseNetTransit(x, rate=1, name=None):
    if rate != 1:
        out_features = x.get_shape().as_list()[-1] * rate
        x = BatchNormalization(center=True, scale=True, name=name + "_bn")(x)
        x = Activation("relu", name=name + "_relu")(x)
        x = Conv3D(
            filters=out_features,
            kernel_size=1,
            strides=1,
            padding="same",
            kernel_initializer="he_normal",
            use_bias=False,
            name=name + "_conv",
        )(x)
    x = AveragePooling3D(pool_size=2, strides=2, padding="same")(x)
    return x


def dense_net(input):
    x = Conv3D(
        filters=24,
        kernel_size=3,
        strides=1,
        kernel_initializer="he_uniform",
        padding="same",
        use_bias=False,
    )(input)
    x = DenseNetUnit3D(x, growth_rate=12, ksize=3, n=4)
    x = DenseNetTransit(x)
    x = DenseNetUnit3D(x, growth_rate=12, ksize=3, n=4)
    x = DenseNetTransit(x)
    x = DenseNetUnit3D(x, growth_rate=12, ksize=3, n=4)
    x = BatchNormalization()(x)
    x = Activation("relu")(x)
    return x


def dense_model(patch_size, num_classes):
    merged_inputs = Input(shape=patch_size + (4,), name="merged_inputs")
    flair = Reshape(patch_size + (1,))(
        Lambda(lambda l: l[:, :, :, :, 0], output_shape=patch_size + (1,))(
            merged_inputs
        ),
    )
    t2 = Reshape(patch_size + (1,))(
        Lambda(lambda l: l[:, :, :, :, 1], output_shape=patch_size + (1,))(
            merged_inputs
        )
    )
    t1 = Lambda(lambda l: l[:, :, :, :, 2:], output_shape=patch_size + (2,))(
        merged_inputs
    )

    flair = dense_net(flair)
    t2 = dense_net(t2)
    t1 = dense_net(t1)

    t2 = concatenate([flair, t2])

    t1 = concatenate([t2, t1])

    tumor = Conv3D(2, kernel_size=1, strides=1, name="tumor")(flair)
    core = Conv3D(3, kernel_size=1, strides=1, name="core")(t2)
    enhancing = Conv3D(
        num_classes, kernel_size=1, strides=1, name="enhancing"
    )(t1)
    net = Model(inputs=merged_inputs, outputs=[tumor, core, enhancing])

    return net


def norm(image):
    image = np.squeeze(image)
    image_nonzero = image[np.nonzero(image)]
    return (image - image_nonzero.mean()) / image_nonzero.std()


def vox_generator_test(all_files):

    path = options["root_path"]

    while 1:
        for file in all_files:
            p = file
            if options["correction"]:
                flair = load_nii(
                    os.path.join(path, file, file + "_flair_corrected.nii.gz")
                ).get_data()
                t2 = load_nii(
                    os.path.join(path, file, file + "_t2_corrected.nii.gz")
                ).get_data()
                t1 = load_nii(
                    os.path.join(path, file, file + "_t1_corrected.nii.gz")
                ).get_data()
                t1ce = load_nii(
                    os.path.join(path, file, file + "_t1ce_corrected.nii.gz")
                ).get_data()
            else:
                flair = load_nii(
                    os.path.join(path, p, p + "_flair.nii.gz")
                ).get_data()

                t2 = load_nii(
                    os.path.join(path, p, p + "_t2.nii.gz")
                ).get_data()

                t1 = load_nii(
                    os.path.join(path, p, p + "_t1.nii.gz")
                ).get_data()

                t1ce = load_nii(
                    os.path.join(path, p, p + "_t1ce.nii.gz")
                ).get_data()
            data = np.array([flair, t2, t1, t1ce])
            data = np.transpose(data, axes=[1, 2, 3, 0])

            data_norm = np.array([norm(flair), norm(t2), norm(t1), norm(t1ce)])
            data_norm = np.transpose(data_norm, axes=[1, 2, 3, 0])

            labels = load_nii(
                os.path.join(path, p, p + "_seg.nii.gz")
            ).get_data()

            yield data, data_norm, labels


def main():
    test_files = []
    with open("test_hgg.txt") as f:
        for line in f:
            test_files.append(line[:-2])

    num_labels = 5
    OFFSET_H = options["offset_h"]
    OFFSET_W = options["offset_w"]
    OFFSET_C = options["offset_c"]
    HSIZE = options["hsize"]
    WSIZE = options["wsize"]
    CSIZE = options["csize"]
    PSIZE = options["psize"]
    SAVE_PATH = options["model_path"]
    model_name = options["model_name"]

    OFFSET_PH = (HSIZE - PSIZE) / 2
    OFFSET_PW = (WSIZE - PSIZE) / 2
    OFFSET_PC = (CSIZE - PSIZE) / 2

    batches_w = int(np.ceil((240 - WSIZE) / float(OFFSET_W))) + 1
    batches_h = int(np.ceil((240 - HSIZE) / float(OFFSET_H))) + 1
    batches_c = int(np.ceil((155 - CSIZE) / float(OFFSET_C))) + 1

    flair_t2_node = tf.placeholder(
        dtype=tf.float32, shape=(None, HSIZE, WSIZE, CSIZE, 2)
    )
    t1_t1ce_node = tf.placeholder(
        dtype=tf.float32, shape=(None, HSIZE, WSIZE, CSIZE, 2)
    )

    if model_name == "dense48":

        flair_t2_15, flair_t2_27 = tf_models.BraTS2ScaleDenseNetConcat_large(
            input=flair_t2_node, name="flair"
        )
        t1_t1ce_15, t1_t1ce_27 = tf_models.BraTS2ScaleDenseNetConcat_large(
            input=t1_t1ce_node, name="t1"
        )
    elif model_name == "no_dense":

        flair_t2_15, flair_t2_27 = tf_models.PlainCounterpart(
            input=flair_t2_node, name="flair"
        )
        t1_t1ce_15, t1_t1ce_27 = tf_models.PlainCounterpart(
            input=t1_t1ce_node, name="t1"
        )

    elif model_name == "dense24":

        flair_t2_15, flair_t2_27 = tf_models.BraTS2ScaleDenseNetConcat(
            input=flair_t2_node, name="flair"
        )
        t1_t1ce_15, t1_t1ce_27 = tf_models.BraTS2ScaleDenseNetConcat(
            input=t1_t1ce_node, name="t1"
        )

    elif model_name == "dense24_nocorrection":

        flair_t2_15, flair_t2_27 = tf_models.BraTS2ScaleDenseNetConcat(
            input=flair_t2_node, name="flair"
        )
        t1_t1ce_15, t1_t1ce_27 = tf_models.BraTS2ScaleDenseNetConcat(
            input=t1_t1ce_node, name="t1"
        )

    else:
        print(" No such model name ")

    t1_t1ce_15 = concatenate([t1_t1ce_15, flair_t2_15])
    t1_t1ce_27 = concatenate([t1_t1ce_27, flair_t2_27])

    t1_t1ce_15 = Conv3D(
        num_labels,
        kernel_size=1,
        strides=1,
        padding="same",
        name="t1_t1ce_15_cls",
    )(t1_t1ce_15)
    t1_t1ce_27 = Conv3D(
        num_labels,
        kernel_size=1,
        strides=1,
        padding="same",
        name="t1_t1ce_27_cls",
    )(t1_t1ce_27)

    t1_t1ce_score = (
        t1_t1ce_15[:, 13:25, 13:25, 13:25, :]
        + t1_t1ce_27[:, 13:25, 13:25, 13:25, :]
    )

    saver = tf.train.Saver()
    data_gen_test = vox_generator_test(test_files)
    dice_whole, dice_core, dice_et = [], [], []
    with tf.Session() as sess:
        saver.restore(sess, SAVE_PATH)
        for i in range(len(test_files)):
            print("predicting %s" % test_files[i])
            x, x_n, y = next(data_gen_test)
            pred = np.zeros([240, 240, 155, 5])
            for hi in range(batches_h):
                offset_h = min(OFFSET_H * hi, 240 - HSIZE)
                offset_ph = int(offset_h + OFFSET_PH)
                for wi in range(batches_w):
                    offset_w = min(OFFSET_W * wi, 240 - WSIZE)
                    offset_pw = int(offset_w + OFFSET_PW)
                    for ci in range(batches_c):
                        offset_c = min(OFFSET_C * ci, 155 - CSIZE)
                        offset_pc = int(offset_c + OFFSET_PC)
                        data = x[
                            offset_h : offset_h + HSIZE,
                            offset_w : offset_w + WSIZE,
                            offset_c : offset_c + CSIZE,
                            :,
                        ]
                        data_norm = x_n[
                            offset_h : offset_h + HSIZE,
                            offset_w : offset_w + WSIZE,
                            offset_c : offset_c + CSIZE,
                            :,
                        ]
                        data_norm = np.expand_dims(data_norm, 0)
                        if not np.max(data) == 0 and np.min(data) == 0:
                            score = sess.run(
                                fetches=t1_t1ce_score,
                                feed_dict={
                                    flair_t2_node: data_norm[:, :, :, :, :2],
                                    t1_t1ce_node: data_norm[:, :, :, :, 2:],
                                    learning_phase(): 0,
                                },
                            )
                            pred[
                                offset_ph : offset_ph + PSIZE,
                                offset_pw : offset_pw + PSIZE,
                                offset_pc : offset_pc + PSIZE,
                                :,
                            ] += np.squeeze(score)
            pred = np.argmax(pred, axis=-1)
            pred = pred.astype(int)
            print("calculating dice...")
            whole_pred = (pred > 0).astype(int)
            whole_gt = (y > 0).astype(int)
            core_pred = (pred == 1).astype(int) + (pred == 4).astype(int)
            core_gt = (y == 1).astype(int) + (y == 4).astype(int)
            et_pred = (pred == 4).astype(int)
            et_gt = (y == 4).astype(int)
            dice_whole_batch = dice_coef_np(whole_gt, whole_pred, 2)
            dice_core_batch = dice_coef_np(core_gt, core_pred, 2)
            try:
                dice_et_batch = dice_coef_np(et_gt, et_pred, 2)
            except ValueError:
                print("Skipped.")
                continue
            dice_whole.append(dice_whole_batch)
            dice_core.append(dice_core_batch)
            dice_et.append(dice_et_batch)
            print(dice_whole_batch)
            print(dice_core_batch)
            print(dice_et_batch)

        dice_whole = np.array(dice_whole)
        dice_core = np.array(dice_core)
        dice_et = np.array(dice_et)

        print("mean dice whole:")
        print(np.mean(dice_whole, axis=0))
        print("mean dice core:")
        print(np.mean(dice_core, axis=0))
        print("mean dice enhance:")
        print(np.mean(dice_et, axis=0))

        np.save(model_name + "_dice_whole", dice_whole)
        np.save(model_name + "_dice_core", dice_core)
        np.save(model_name + "_dice_enhance", dice_et)
        print("pred saved")


if __name__ == "__main__":
    main()
